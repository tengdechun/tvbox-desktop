"""
增强下载管理器 —— 多线程 / 断点续传 / 批量下载 / 进度通知
支持 HTTP/HTTPS, 自动重试, 速度限制
"""

import os
import sys
import time
import json
import threading
import requests
from typing import Optional, Callable, Dict, List
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed


@dataclass
class DownloadTask:
    """下载任务"""
    task_id: int
    vod_name: str
    episode_name: str
    url: str
    file_path: str
    total_size: int = 0
    downloaded: int = 0
    speed: float = 0.0
    status: str = "pending"  # pending / downloading / paused / completed / failed / cancelled
    error: str = ""
    start_time: float = 0.0
    eta: float = 0.0
    threads: int = 4
    _stop_event: threading.Event = field(default_factory=threading.Event)
    _pause_event: threading.Event = field(default_factory=threading.Event)
    _temp_file: str = ""


class DownloadManager:
    """多线程下载管理器"""

    def __init__(self, db, max_concurrent: int = 3):
        self.db = db
        self.max_concurrent = max_concurrent
        self._tasks: Dict[int, DownloadTask] = {}
        self._executor = ThreadPoolExecutor(max_workers=max_concurrent * 2)
        self._lock = threading.Lock()
        self._progress_callbacks: List[Callable] = []
        self._chunk_size = 65536  # 64KB
        self._speed_limit = 0  # 0 = 无限制 (bytes/s)

    def add_progress_callback(self, callback: Callable):
        """添加进度回调"""
        self._progress_callbacks.append(callback)

    def _notify_progress(self, task: DownloadTask):
        """通知进度更新"""
        for cb in self._progress_callbacks:
            try:
                cb(task)
            except Exception:
                pass

    def add_download(self, vod_name: str, episode_name: str, url: str,
                     headers: dict = None, threads: int = 4) -> int:
        """添加下载任务"""
        download_dir = self._get_download_dir()
        safe_name = "".join(c for c in vod_name if c not in r'\/:*?"<>|')[:80]
        safe_ep = "".join(c for c in episode_name if c not in r'\/:*?"<>|')[:50]
        file_path = os.path.join(download_dir, f"{safe_name}_{safe_ep}.mp4")

        dl_id = self.db.add_download(vod_name, episode_name, url, file_path)

        task = DownloadTask(
            task_id=dl_id,
            vod_name=vod_name,
            episode_name=episode_name,
            url=url,
            file_path=file_path,
            threads=threads,
        )
        task._temp_file = file_path + '.tmp'
        task._pause_event.set()  # 默认不暂停

        with self._lock:
            self._tasks[dl_id] = task

        # 提交下载任务
        self._executor.submit(self._download, task, headers)
        return dl_id

    def add_batch_download(self, vod_name: str, episodes: List[dict],
                           headers: dict = None) -> List[int]:
        """批量下载
        episodes: [{name, url}, ...]
        """
        task_ids = []
        for ep in episodes:
            tid = self.add_download(vod_name, ep.get('name', ''), ep.get('url', ''), headers)
            task_ids.append(tid)
        return task_ids

    def _download(self, task: DownloadTask, headers: dict = None):
        """执行下载"""
        task.status = "downloading"
        task.start_time = time.time()
        self._notify_progress(task)

        try:
            req_headers = {
                "User-Agent": "Mozilla/5.0 (Linux; Android 12; Pixel 6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
            }
            if headers:
                req_headers.update(headers)

            # 先 HEAD 请求获取文件大小
            try:
                head_resp = requests.head(task.url, headers=req_headers, timeout=15, allow_redirects=True)
                task.total_size = int(head_resp.headers.get('Content-Length', 0))
                accept_ranges = head_resp.headers.get('Accept-Ranges', '').lower() == 'bytes'
            except Exception:
                task.total_size = 0
                accept_ranges = False

            # 检查是否已有临时文件 (断点续传)
            resume_pos = 0
            if os.path.exists(task._temp_file):
                resume_pos = os.path.getsize(task._temp_file)
                if task.total_size > 0 and resume_pos >= task.total_size:
                    # 已经下载完成, 重命名
                    os.rename(task._temp_file, task.file_path)
                    task.status = "completed"
                    task.downloaded = task.total_size
                    self.db.update_download_status(task.task_id, 'completed', task.total_size, task.total_size)
                    self._notify_progress(task)
                    return

            if accept_ranges and task.total_size > 0 and task.threads > 1 and task.total_size > 1024 * 1024:
                # 多线程下载
                self._download_multi_thread(task, req_headers, resume_pos)
            else:
                # 单线程下载
                self._download_single_thread(task, req_headers, resume_pos)

            # 下载完成, 重命名临时文件
            if not task._stop_event.is_set() and os.path.exists(task._temp_file):
                if os.path.exists(task.file_path):
                    os.remove(task.file_path)
                os.rename(task._temp_file, task.file_path)
                task.status = "completed"
                task.downloaded = task.total_size if task.total_size > 0 else task.downloaded
                self.db.update_download_status(task.task_id, 'completed', task.downloaded, task.total_size)

        except Exception as e:
            task.status = "failed"
            task.error = str(e)
            self.db.update_download_status(task.task_id, 'failed', task.downloaded, task.total_size)

        self._notify_progress(task)

    def _download_single_thread(self, task: DownloadTask, headers: dict, resume_pos: int = 0):
        """单线程下载"""
        mode = 'ab' if resume_pos > 0 else 'wb'
        if resume_pos > 0:
            headers['Range'] = f'bytes={resume_pos}-'

        resp = requests.get(task.url, headers=headers, stream=True, timeout=30)
        resp.raise_for_status()

        if not task.total_size:
            task.total_size = int(resp.headers.get('Content-Length', 0)) + resume_pos

        task.downloaded = resume_pos
        last_time = time.time()
        last_bytes = resume_pos

        with open(task._temp_file, mode) as f:
            for chunk in resp.iter_content(self._chunk_size):
                if task._stop_event.is_set():
                    task.status = "cancelled"
                    self.db.update_download_status(task.task_id, 'cancelled', task.downloaded, task.total_size)
                    return

                # 暂停等待
                task._pause_event.wait()

                if chunk:
                    f.write(chunk)
                    task.downloaded += len(chunk)

                    # 计算速度
                    now = time.time()
                    elapsed = now - last_time
                    if elapsed >= 0.5:
                        task.speed = (task.downloaded - last_bytes) / elapsed
                        if task.speed > 0 and task.total_size > 0:
                            remaining = task.total_size - task.downloaded
                            task.eta = remaining / task.speed
                        last_time = now
                        last_bytes = task.downloaded

                        # 速度限制
                        if self._speed_limit > 0 and task.speed > self._speed_limit:
                            time.sleep(0.1)

                        # 更新数据库 (每秒更新一次)
                        self.db.update_download_status(
                            task.task_id, 'downloading', task.downloaded, task.total_size
                        )
                        self._notify_progress(task)

    def _download_multi_thread(self, task: DownloadTask, headers: dict, resume_pos: int = 0):
        """多线程分块下载"""
        total = task.total_size
        num_threads = task.threads
        chunk_size = total // num_threads

        # 创建各线程的起止位置
        ranges = []
        for i in range(num_threads):
            start = i * chunk_size
            end = (i + 1) * chunk_size - 1 if i < num_threads - 1 else total - 1
            if start < resume_pos:
                start = resume_pos
            if start <= end:
                ranges.append((i, start, end))

        # 预分配文件空间
        with open(task._temp_file, 'wb') as f:
            f.truncate(total)

        # 各线程下载进度
        thread_progress = {i: 0 for i, _, _ in ranges}
        progress_lock = threading.Lock()
        last_update = {'time': time.time(), 'bytes': 0}

        def download_chunk(thread_id: int, start: int, end: int):
            """下载一个分块"""
            if task._stop_event.is_set():
                return

            chunk_headers = headers.copy()
            chunk_headers['Range'] = f'bytes={start}-{end}'

            try:
                resp = requests.get(task.url, headers=chunk_headers, stream=True, timeout=30)
                resp.raise_for_status()

                offset = start
                for data in resp.iter_content(self._chunk_size):
                    if task._stop_event.is_set():
                        return
                    task._pause_event.wait()

                    if data:
                        with open(task._temp_file, 'r+b') as f:
                            f.seek(offset)
                            f.write(data)

                        offset += len(data)
                        with progress_lock:
                            thread_progress[thread_id] += len(data)
                            task.downloaded = resume_pos + sum(thread_progress.values())

                            now = time.time()
                            elapsed = now - last_update['time']
                            if elapsed >= 0.5:
                                task.speed = (task.downloaded - last_update['bytes']) / elapsed
                                if task.speed > 0 and task.total_size > 0:
                                    remaining = task.total_size - task.downloaded
                                    task.eta = remaining / task.speed
                                last_update['time'] = now
                                last_update['bytes'] = task.downloaded

                                self.db.update_download_status(
                                    task.task_id, 'downloading', task.downloaded, task.total_size
                                )
                                self._notify_progress(task)

            except Exception as e:
                task.error = f"线程{thread_id}失败: {e}"

        # 使用线程池并行下载
        with ThreadPoolExecutor(max_workers=num_threads) as pool:
            futures = [pool.submit(download_chunk, tid, start, end) for tid, start, end in ranges]
            for f in as_completed(futures):
                if task._stop_event.is_set():
                    break

    def pause_download(self, task_id: int) -> bool:
        """暂停下载"""
        with self._lock:
            task = self._tasks.get(task_id)
            if task and task.status == "downloading":
                task._pause_event.clear()
                task.status = "paused"
                self.db.update_download_status(task_id, 'paused', task.downloaded, task.total_size)
                self._notify_progress(task)
                return True
        return False

    def resume_download(self, task_id: int) -> bool:
        """恢复下载"""
        with self._lock:
            task = self._tasks.get(task_id)
            if task and task.status == "paused":
                task._pause_event.set()
                task.status = "downloading"
                self.db.update_download_status(task_id, 'downloading', task.downloaded, task.total_size)
                self._notify_progress(task)
                return True
        return False

    def cancel_download(self, task_id: int) -> bool:
        """取消下载"""
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task._stop_event.set()
                task._pause_event.set()  # 解除暂停以便线程退出
                task.status = "cancelled"
                self.db.update_download_status(task_id, 'cancelled', task.downloaded, task.total_size)
                # 清理临时文件
                try:
                    if os.path.exists(task._temp_file):
                        os.remove(task._temp_file)
                except Exception:
                    pass
                self._notify_progress(task)
                return True
        return False

    def retry_download(self, task_id: int, headers: dict = None) -> bool:
        """重试下载"""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                # 从数据库恢复任务
                downloads = self.db.get_downloads()
                for d in downloads:
                    if d['id'] == task_id:
                        task = DownloadTask(
                            task_id=d['id'],
                            vod_name=d['vod_name'],
                            episode_name=d.get('episode_name', ''),
                            url=d['url'],
                            file_path=d['file_path'],
                            total_size=d.get('file_size', 0),
                            downloaded=d.get('downloaded', 0),
                        )
                        task._temp_file = d['file_path'] + '.tmp'
                        task._pause_event.set()
                        self._tasks[task_id] = task
                        break

            if task:
                task._stop_event.clear()
                task.error = ""
                self._executor.submit(self._download, task, headers)
                return True
        return False

    def get_task_status(self, task_id: int) -> Optional[dict]:
        """获取任务状态"""
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                return {
                    "id": task.task_id,
                    "vod_name": task.vod_name,
                    "episode_name": task.episode_name,
                    "status": task.status,
                    "total_size": task.total_size,
                    "downloaded": task.downloaded,
                    "speed": round(task.speed, 1),
                    "eta": round(task.eta, 1),
                    "progress": round(task.downloaded / task.total_size * 100, 1) if task.total_size > 0 else 0,
                    "error": task.error,
                }
        return None

    def get_all_status(self) -> List[dict]:
        """获取所有任务状态"""
        with self._lock:
            return [self.get_task_status(tid) for tid in list(self._tasks.keys())]

    def set_speed_limit(self, limit_kbps: int):
        """设置速度限制 (KB/s)"""
        self._speed_limit = limit_kbps * 1024 if limit_kbps > 0 else 0

    def _get_download_dir(self) -> str:
        """获取下载目录"""
        if os.name == 'nt':
            base = os.environ.get('USERPROFILE', os.path.expanduser('~'))
            dl_dir = os.path.join(base, 'Downloads', 'TVBoxDesktop')
        else:
            dl_dir = os.path.join(os.path.expanduser('~'), 'Downloads', 'TVBoxDesktop')
        os.makedirs(dl_dir, exist_ok=True)
        return dl_dir

    def shutdown(self):
        """关闭下载管理器"""
        with self._lock:
            for task in self._tasks.values():
                if task.status == "downloading":
                    task._stop_event.set()
                    task._pause_event.set()
        self._executor.shutdown(wait=False, cancel_futures=True)
