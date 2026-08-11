"""
JAR 爬虫引擎 —— 通过 JPype 加载 Java Spider 类
支持 TVBox/CatVod JAR 源 (Type 1)
自动处理 DEX 到 JVM 字节码的转换
内嵌精简 JRE, 无需用户安装 Java
需要安装: pip install JPype1
可选安装: pip install enjarify-adapter (DEX 转换)
"""

import os
import sys
import json
import tempfile
import subprocess
import zipfile
import shutil
import platform
from typing import Optional, Dict, Any, List

import requests

# JPype 条件导入
_jpype_available = False
try:
    import jpype
    import jpype.imports
    _jpype_available = True
except ImportError:
    pass


# ======== 内嵌 JRE 检测 ========

def _find_bundled_jre() -> Optional[str]:
    """查找内嵌的 JRE 路径

    查找顺序:
    1. PyInstaller 打包后: _MEIPASS/jre/
    2. 开发模式: 项目目录/jre/
    3. 用户数据目录: %LOCALAPPDATA%/TVBoxDesktop/jre/
    """
    candidates = []

    # PyInstaller 打包模式
    if getattr(sys, 'frozen', False):
        base = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
        candidates.append(os.path.join(base, 'jre'))
        candidates.append(os.path.join(os.path.dirname(sys.executable), 'jre'))

    # 开发模式
    dev_dir = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(dev_dir, 'jre'))

    # 用户数据目录
    if os.name == 'nt':
        local_app = os.environ.get('LOCALAPPDATA', '')
        if local_app:
            candidates.append(os.path.join(local_app, 'TVBoxDesktop', 'jre'))
    else:
        candidates.append(os.path.join(os.path.expanduser('~'), '.tvboxdesktop', 'jre'))

    for jre_dir in candidates:
        jvm_path = _validate_jre_dir(jre_dir)
        if jvm_path:
            return jvm_path

    return None


def _validate_jre_dir(jre_dir: str) -> Optional[str]:
    """验证 JRE 目录, 返回 jvm.dll/libjvm.so 路径"""
    if not os.path.isdir(jre_dir):
        return None

    if os.name == 'nt':
        # Windows: jre/bin/server/jvm.dll 或 jre/bin/client/jvm.dll
        for pattern in ['bin/server/jvm.dll', 'bin/client/jvm.dll',
                        'bin/server/jvm.dll', 'jvm.dll']:
            path = os.path.join(jre_dir, pattern)
            if os.path.exists(path):
                return path
        # 也检查 bin/java.exe
        if os.path.exists(os.path.join(jre_dir, 'bin', 'java.exe')):
            return jre_dir
    else:
        # Linux/Mac: lib/server/libjvm.so 或 lib/libjvm.so
        for pattern in ['lib/server/libjvm.so', 'lib/libjvm.so',
                        'lib/server/libjvm.dylib', 'lib/libjvm.dylib',
                        'bin/java']:
            path = os.path.join(jre_dir, pattern)
            if os.path.exists(path):
                return path

    return None


def _find_system_java() -> Optional[str]:
    """查找系统安装的 Java"""
    # 方法 1: jpype 自带的 JVM 查找
    if _jpype_available:
        try:
            from jpype._jvmfinder import JVMFinder
            finder = JVMFinder()
            jvm_path = finder.get_jvm_path()
            if jvm_path and os.path.exists(jvm_path):
                return jvm_path
        except Exception:
            pass

    # 方法 2: 检查 JAVA_HOME
    java_home = os.environ.get('JAVA_HOME', '')
    if java_home:
        jvm = _validate_jre_dir(java_home)
        if jvm:
            return jvm

    # 方法 3: 尝试 where/which java
    try:
        cmd = 'where java' if os.name == 'nt' else 'which java'
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            java_path = result.stdout.strip().split('\n')[0].strip()
            java_dir = os.path.dirname(os.path.dirname(java_path))
            jvm = _validate_jre_dir(java_dir)
            if jvm:
                return jvm
    except Exception:
        pass

    return None


def _get_jvm_path() -> Optional[str]:
    """获取 JVM 路径: 内嵌 JRE > 系统 Java"""
    # 1. 优先使用内嵌 JRE
    bundled = _find_bundled_jre()
    if bundled:
        return bundled

    # 2. 回退到系统 Java
    system_java = _find_system_java()
    if system_java:
        return system_java

    return None


# 存储找到的 JVM 路径
_jvm_path_cache: Optional[str] = None


# ======== CatVod API 桩 Java 源代码 ========

STUB_CONTEXT_JAVA = '''
package com.github.catvod.crawler;

public class Context {
    private static Context instance;
    public static Context getInstance() {
        if (instance == null) instance = new Context();
        return instance;
    }
    public String getPackageName() { return "com.github.tvbox"; }
    public String getFilesDir() { return System.getProperty("java.io.tmpdir"); }
    public String getCacheDir() { return System.getProperty("java.io.tmpdir"); }
    public String getCodeCacheDir() { return System.getProperty("java.io.tmpdir"); }
    public Object getSystemService(String name) { return null; }
}
'''

STUB_SPIDER_JAVA = '''
package com.github.catvod.crawler;

import org.json.JSONObject;
import org.json.JSONArray;
import java.util.List;
import java.util.Map;

public abstract class Spider {
    public void init(Context context) {}
    public void init(Context context, Map<String, String> config) {}
    public JSONObject homeContent(boolean filter) { return new JSONObject(); }
    public JSONObject homeVideoContent() { return new JSONObject(); }
    public JSONObject categoryContent(String tid, int pg, boolean filter, Map<String, String> extend) { return new JSONObject(); }
    public JSONObject searchContent(String key, boolean quick, int pg) { return new JSONObject(); }
    public JSONObject detailContent(List<String> ids) { return new JSONObject(); }
    public JSONObject playerContent(String flag, String id, List<String> vipFlags) { return new JSONObject(); }
    public String liveContent(String url) { return ""; }
    public JSONArray proxy(Map<String, String> params) { return new JSONArray(); }
    public JSONObject action(String action) { return new JSONObject(); }
    public boolean manualVideoCheck() { return false; }
    public boolean isVideoFormat(String url) { return false; }
    public void destroy() {}
}
'''


class JarSpiderEngine:
    """JAR 爬虫引擎

    工作流程:
    1. 下载 JAR 文件 (HTTP / base64 / 本地路径)
    2. 检测是否为 DEX 格式, 必要时转换为 JVM 字节码
    3. 准备 classpath (spider JAR + CatVod 桩 + org.json)
    4. 启动 JVM (通过 JPype)
    5. 加载 Spider 类, 实例化, 调用 init
    6. 调用各 Spider 接口方法, 转换返回值为 Python 字典
    """

    ORG_JSON_MAVEN = "https://repo1.maven.org/maven2/org/json/json/20240303/json-20240303.jar"

    def __init__(self):
        self._jar_cache: Dict[str, str] = {}
        self._converted_jars: Dict[str, str] = {}
        self._stub_jar: str = ""
        self._org_json_jar: str = ""
        self._jvm_classpaths: List[str] = []
        self._loaded_classes: Dict[str, Any] = {}

    def is_available(self) -> bool:
        """检查 JAR 支持是否可用 (JPype 是否已安装)"""
        return _jpype_available

    def get_requirements_message(self) -> str:
        """返回安装提示"""
        jvm = _get_jvm_path()
        if jvm:
            return "JAR 源就绪: 已检测到 Java 运行时 (" + jvm + ")"
        return (
            "JAR 源需要 Java 运行时 (JRE/JDK 11+)\n"
            "请将精简 JRE 放在 EXE 同级 jre/ 目录下, 或安装系统 Java\n"
            "DEX 格式 JAR 还需 dex2jar 或 enjarify"
        )

    # ======== JAR 下载与缓存 ========

    def _download_jar(self, jar_url: str) -> str:
        """下载或获取缓存的 JAR 文件"""
        if jar_url in self._jar_cache:
            cached = self._jar_cache[jar_url]
            if os.path.exists(cached):
                return cached

        # base64 内嵌
        if jar_url.startswith("base64://"):
            import base64
            jar_data = base64.b64decode(jar_url[9:])
            fd, tmp_path = tempfile.mkstemp(suffix=".jar", prefix="tvbox_jar_")
            with os.fdopen(fd, "wb") as f:
                f.write(jar_data)
            self._jar_cache[jar_url] = tmp_path
            return tmp_path

        # HTTP/HTTPS 下载
        if jar_url.startswith("http"):
            resp = requests.get(
                jar_url, timeout=30,
                headers={"User-Agent": "Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36"}
            )
            resp.raise_for_status()
            fd, tmp_path = tempfile.mkstemp(suffix=".jar", prefix="tvbox_jar_")
            with os.fdopen(fd, "wb") as f:
                f.write(resp.content)
            self._jar_cache[jar_url] = tmp_path
            return tmp_path

        # 本地文件
        if os.path.exists(jar_url):
            self._jar_cache[jar_url] = jar_url
            return jar_url

        raise FileNotFoundError("JAR 文件不存在: " + jar_url)

    # ======== DEX 检测与转换 ========

    def _is_dex_jar(self, jar_path: str) -> bool:
        """检查 JAR 是否包含 DEX 字节码"""
        try:
            with zipfile.ZipFile(jar_path, 'r') as zf:
                names = zf.namelist()
                return 'classes.dex' in names
        except Exception:
            return False

    def _convert_dex_to_jar(self, dex_jar_path: str) -> Optional[str]:
        """将 DEX JAR 转换为标准 JVM JAR

        尝试顺序:
        1. dex2jar (d2j-dex2jar 命令行工具)
        2. enjarify (Python 包)
        """
        # 方法 1: dex2jar
        for cmd in ['d2j-dex2jar', 'd2j-dex2jar.bat', 'd2j-dex2jar.sh']:
            try:
                result = subprocess.run(
                    [cmd, '--version'],
                    capture_output=True, timeout=5
                )
                if result.returncode == 0 or result.stdout:
                    fd, output_path = tempfile.mkstemp(
                        suffix=".jar", prefix="tvbox_conv_"
                    )
                    os.close(fd)
                    os.remove(output_path)

                    result = subprocess.run(
                        [cmd, '-f', '-o', output_path, dex_jar_path],
                        capture_output=True, timeout=120
                    )
                    if result.returncode == 0 and os.path.exists(output_path):
                        self._converted_jars[dex_jar_path] = output_path
                        return output_path

                    try:
                        os.remove(output_path)
                    except Exception:
                        pass
            except FileNotFoundError:
                continue
            except Exception:
                continue

        # 方法 2: enjarify (Python 包)
        try:
            from enjarify import enjarify  # type: ignore
            fd, output_path = tempfile.mkstemp(
                suffix=".jar", prefix="tvbox_enj_"
            )
            os.close(fd)

            enjarify(dex_jar_path, output_path=output_path)
            if os.path.exists(output_path):
                self._converted_jars[dex_jar_path] = output_path
                return output_path

            try:
                os.remove(output_path)
            except Exception:
                pass
        except ImportError:
            pass
        except Exception:
            try:
                os.remove(output_path)
            except Exception:
                pass

        return None

    # ======== CatVod 桩 JAR 构建 ========

    def _ensure_stub_jar(self) -> str:
        """创建或获取 CatVod API 桩 JAR"""
        if self._stub_jar and os.path.exists(self._stub_jar):
            return self._stub_jar

        # 检查内嵌的预编译桩
        bundled = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            'static', 'catvod_stub.jar'
        )
        if os.path.exists(bundled):
            self._stub_jar = bundled
            return bundled

        # 检查缓存目录
        cached_stub = os.path.join(tempfile.gettempdir(), "tvbox_catvod_stub.jar")
        if os.path.exists(cached_stub):
            self._stub_jar = cached_stub
            return cached_stub

        # 动态编译桩类 (需要 JDK)
        stub_jar = self._compile_stub_classes()
        if stub_jar:
            self._stub_jar = stub_jar
            return stub_jar

        return ""

    def _compile_stub_classes(self) -> str:
        """动态编译 CatVod 桩 Java 源代码"""
        stub_dir = tempfile.mkdtemp(prefix="tvbox_stub_")

        # 写入 Java 源文件
        sources = []
        for name, code in [
            ("Context.java", STUB_CONTEXT_JAVA),
            ("Spider.java", STUB_SPIDER_JAVA),
        ]:
            # 创建包目录结构
            pkg_dir = os.path.join(stub_dir, "com", "github", "catvod", "crawler")
            os.makedirs(pkg_dir, exist_ok=True)
            src_path = os.path.join(pkg_dir, name)
            with open(src_path, 'w', encoding='utf-8') as f:
                f.write(code.strip())
            sources.append(src_path)

        # 编译 (需要 org.json 在 classpath)
        org_json_jar = self._ensure_org_json_jar()
        cp_args = []
        if org_json_jar:
            cp_args = ['-cp', org_json_jar]

        try:
            result = subprocess.run(
                ['javac'] + cp_args + ['-d', stub_dir] + sources,
                capture_output=True, timeout=15
            )
            if result.returncode != 0:
                print("[JarSpider] 桩类编译失败: " + result.stderr.decode('utf-8', errors='replace')[:200])
                shutil.rmtree(stub_dir, ignore_errors=True)
                return ""
        except FileNotFoundError:
            print("[JarSpider] 未找到 javac, 请安装 JDK")
            shutil.rmtree(stub_dir, ignore_errors=True)
            return ""
        except Exception as e:
            print("[JarSpider] 编译异常: " + str(e))
            shutil.rmtree(stub_dir, ignore_errors=True)
            return ""

        # 打包 JAR
        stub_jar = os.path.join(tempfile.gettempdir(), "tvbox_catvod_stub.jar")
        try:
            subprocess.run(
                ['jar', 'cf', stub_jar, '-C', stub_dir, '.'],
                capture_output=True, timeout=10
            )
        except Exception:
            pass

        shutil.rmtree(stub_dir, ignore_errors=True)

        if os.path.exists(stub_jar):
            return stub_jar
        return ""

    def _ensure_org_json_jar(self) -> str:
        """获取 org.json JAR (从 Maven 中央仓库下载)"""
        if self._org_json_jar and os.path.exists(self._org_json_jar):
            return self._org_json_jar

        cached = os.path.join(tempfile.gettempdir(), "org_json.jar")
        if os.path.exists(cached):
            self._org_json_jar = cached
            return cached

        try:
            resp = requests.get(self.ORG_JSON_MAVEN, timeout=15)
            if resp.status_code == 200:
                with open(cached, 'wb') as f:
                    f.write(resp.content)
                self._org_json_jar = cached
                return cached
        except Exception:
            pass

        return ""

    # ======== JVM 管理 ========

    def _start_jvm(self, classpath: List[str]):
        """启动 JVM (如果尚未启动)

        优先使用内嵌精简 JRE, 其次使用系统 Java
        """
        if not _jpype_available:
            raise RuntimeError("JPype 未安装, 请运行: pip install JPype1")

        if jpype.isJVMStarted():
            # JVM 已启动, 动态添加 classpath
            self._add_to_classpath(classpath)
            return

        global _jvm_path_cache
        if _jvm_path_cache is None:
            _jvm_path_cache = _get_jvm_path()

        jvm_args = {
            'classpath': classpath,
            'convertStrings': True,
        }
        if _jvm_path_cache:
            jvm_args['jvmpath'] = _jvm_path_cache

        try:
            jpype.startJVM(**jvm_args)
            self._jvm_classpaths = list(classpath)
        except Exception as e:
            raise RuntimeError(
                "JVM 启动失败: " + str(e) +
                "\n请确保已安装 Java 11+ 或内嵌 JRE 可用"
            )

    def _add_to_classpath(self, new_paths: List[str]):
        """向已运行的 JVM 添加 classpath 条目"""
        try:
            URLClassLoader = jpype.JClass("java.net.URLClassLoader")
            URL = jpype.JClass("java.net.URL")
            File = jpype.JClass("java.io.File")

            cl = URLClassLoader.getSystemClassLoader()

            for path in new_paths:
                if path in self._jvm_classpaths:
                    continue
                if not os.path.exists(path):
                    continue
                url = File(path).toURI().toURL()
                method = URLClassLoader.getDeclaredMethod("addURL", URL)
                method.setAccessible(True)
                method.invoke(cl, url)
                self._jvm_classpaths.append(path)
        except Exception as e:
            print("[JarSpider] 添加 classpath 失败 (非致命): " + str(e))

    # ======== Spider 加载 ========

    def load_spider(self, jar_url: str, class_name: str, ext: str = "") -> Any:
        """加载并初始化 Spider 实例

        Args:
            jar_url: JAR 文件的 URL 或本地路径
            class_name: Spider 类名 (如 "csp_AppYsV2" 或完整类名)
            ext: 初始化配置 (JSON 字符串)

        Returns:
            Java Spider 对象
        """
        # 1. 下载 JAR
        jar_path = self._download_jar(jar_url)

        # 2. DEX 检测与转换
        if self._is_dex_jar(jar_path):
            # 检查是否已转换过
            if jar_path in self._converted_jars:
                jar_path = self._converted_jars[jar_path]
            else:
                converted = self._convert_dex_to_jar(jar_path)
                if converted:
                    jar_path = converted
                else:
                    raise RuntimeError(
                        "JAR 包含 DEX 字节码 (Android 专用), 需要安装转换工具:\n"
                        "  方式1: 安装 dex2jar (https://github.com/pxb1988/dex2jar)\n"
                        "  方式2: pip install enjarify"
                    )

        # 3. 准备 classpath
        classpath = [jar_path]
        stub_jar = self._ensure_stub_jar()
        if stub_jar:
            classpath.append(stub_jar)
        org_json_jar = self._ensure_org_json_jar()
        if org_json_jar:
            classpath.append(org_json_jar)

        # 4. 启动 JVM
        self._start_jvm(classpath)

        # 5. 解析类名
        full_name = self._resolve_class_name(class_name)

        # 6. 加载 Spider 类
        cache_key = full_name + "|" + jar_path
        if cache_key in self._loaded_classes:
            return self._loaded_classes[cache_key]

        try:
            SpiderClass = jpype.JClass(full_name)
        except Exception:
            try:
                SpiderClass = jpype.JClass(class_name)
            except Exception as e:
                raise RuntimeError(
                    "无法加载 Spider 类: " + class_name + " (" + full_name + "): " + str(e)
                )

        # 7. 实例化
        spider = SpiderClass()

        # 8. 调用 init
        try:
            Context = jpype.JClass("com.github.catvod.crawler.Context")
            ctx = Context.getInstance()

            if ext:
                HashMap = jpype.JClass("java.util.HashMap")
                config_map = HashMap()
                try:
                    ext_dict = json.loads(ext) if isinstance(ext, str) else ext
                    if isinstance(ext_dict, dict):
                        for k, v in ext_dict.items():
                            config_map[str(k)] = str(v) if v is not None else ""
                except Exception:
                    pass
                spider.init(ctx, config_map)
            else:
                spider.init(ctx)
        except Exception as e:
            print("[JarSpider] init 调用失败 (非致命): " + str(e))

        self._loaded_classes[cache_key] = spider
        return spider

    def _resolve_class_name(self, class_name: str) -> str:
        """解析 Spider 类名

        TVBox 约定:
        - "csp_XXX" -> "com.github.catvod.spider.XXX"
        - 包含 "." 的视为完整类名
        - 其他视为简短名, 添加默认包前缀
        """
        if class_name.startswith("csp_"):
            return "com.github.catvod.spider." + class_name[4:]
        if "." in class_name:
            return class_name
        return "com.github.catvod.spider." + class_name

    # ======== 结果转换 ========

    def _convert_result(self, result: Any) -> Any:
        """将 Java 对象转换为 Python 类型"""
        if result is None:
            return {}

        # Python 原生类型
        if isinstance(result, (bool, int, float)):
            return result

        # 字符串
        if isinstance(result, str):
            try:
                return json.loads(result)
            except Exception:
                return {"result": result}

        # Java 对象 (JSONObject / JSONArray 等)
        if hasattr(result, 'toString'):
            try:
                result_str = str(result.toString())
            except Exception:
                result_str = str(result)
            try:
                return json.loads(result_str)
            except Exception:
                return {"result": result_str}

        return result

    def _to_java_map(self, py_dict: dict) -> Any:
        """Python 字典 -> Java HashMap"""
        HashMap = jpype.JClass("java.util.HashMap")
        java_map = HashMap()
        if py_dict:
            for k, v in py_dict.items():
                java_map[str(k)] = str(v) if v is not None else ""
        return java_map

    def _to_java_list(self, py_list: list) -> Any:
        """Python 列表 -> Java ArrayList"""
        ArrayList = jpype.JClass("java.util.ArrayList")
        java_list = ArrayList()
        if py_list:
            for item in py_list:
                java_list.add(str(item))
        return java_list

    # ======== Spider 接口调用 ========

    def call_home_content(self, spider: Any, filter: bool = False) -> dict:
        try:
            return self._convert_result(spider.homeContent(filter))
        except Exception as e:
            return {"error": str(e)}

    def call_home_video_content(self, spider: Any) -> dict:
        try:
            return self._convert_result(spider.homeVideoContent())
        except Exception as e:
            return {"error": str(e)}

    def call_category_content(self, spider: Any, tid: str, pg: int,
                               filter: bool = False, extend: dict = None) -> dict:
        try:
            ext_map = self._to_java_map(extend) if extend else self._to_java_map({})
            return self._convert_result(
                spider.categoryContent(tid, int(pg), filter, ext_map)
            )
        except Exception as e:
            return {"error": str(e)}

    def call_search_content(self, spider: Any, key: str,
                             quick: bool = False, pg: int = 1) -> dict:
        try:
            return self._convert_result(spider.searchContent(key, quick, int(pg)))
        except Exception as e:
            return {"error": str(e)}

    def call_detail_content(self, spider: Any, ids: list) -> dict:
        try:
            id_list = self._to_java_list(ids)
            return self._convert_result(spider.detailContent(id_list))
        except Exception as e:
            return {"error": str(e)}

    def call_player_content(self, spider: Any, flag: str, id: str,
                             vip_flags: list = None) -> dict:
        try:
            flag_list = self._to_java_list(vip_flags) if vip_flags else self._to_java_list([])
            return self._convert_result(
                spider.playerContent(flag, id, flag_list)
            )
        except Exception as e:
            return {"error": str(e)}

    def call_live_content(self, spider: Any, url: str) -> str:
        try:
            result = spider.liveContent(url)
            if isinstance(result, str):
                return result
            return str(result) if result else ""
        except Exception:
            return ""

    def call_is_video_format(self, spider: Any, url: str) -> bool:
        try:
            return bool(spider.isVideoFormat(url))
        except Exception:
            return False

    def call_manual_video_check(self, spider: Any) -> bool:
        try:
            return bool(spider.manualVideoCheck())
        except Exception:
            return False

    def call_destroy(self, spider: Any):
        try:
            spider.destroy()
        except Exception:
            pass


# ======== 全局引擎实例 ========

_engine: Optional[JarSpiderEngine] = None


def get_engine() -> JarSpiderEngine:
    """获取全局 JAR 爬虫引擎实例"""
    global _engine
    if _engine is None:
        _engine = JarSpiderEngine()
    return _engine


def is_jar_support_available() -> bool:
    """检查 JAR 支持是否可用"""
    return _jpype_available


def get_install_guide() -> str:
    """返回安装指南"""
    jvm = _get_jvm_path()
    if jvm:
        return "JAR 源已就绪, 无需额外安装。Java 运行时: " + jvm
    return (
        "JAR 源需要 Java 运行时 (JRE/JDK 11+):\n"
        "方式1 (推荐): 将精简 JRE 放在 EXE 同级 jre/ 目录下\n"
        "  - 使用 jlink 创建: jlink --add-modules java.base,java.xml,... --output jre\n"
        "方式2: 安装系统 Java: https://adoptium.net/\n"
        "(可选) DEX 转换: dex2jar 或 pip install enjarify"
    )
