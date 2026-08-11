/**
 * TVBox Desktop v5.0 —— 完整前端应用逻辑
 * 功能: 点播/搜索/直播/收藏/历史/下载/设置
 * 播放器: 全屏/快捷键/自动连播/倍速/音量/画中画/进度记忆/弹幕/字幕/截图/AB回放
 * 搜索: 历史记录/智能建议/多站聚合
 * 直播: 频道搜索/收藏/历史/EPG节目单
 * 下载: 多线程/断点续传/批量下载/暂停恢复
 */

const App = {
    // ======== 状态 ========
    api: null,
    currentSiteKey: null,
    currentCategory: null,
    currentPage: 1,
    currentDetail: null,
    currentEpisodes: [],
    currentEpIndex: 0,
    currentLine: 0,
    currentPlayUrl: '',
    currentPlayFlag: '',
    currentPlayVid: '',
    liveData: null,
    currentLiveChannel: null,
    currentLiveGroup: null,
    liveChannelList: [],
    liveChannelIndex: -1,
    hls: null,
    liveHls: null,
    currentFilters: {},
    playStartTime: 0,
    historyTimer: null,
    downloadTimer: null,
    autoNext: true,
    resumePlay: true,
    isFullscreen: false,
    isMuted: false,
    lastVolume: 100,
    liveTab: 'all',
    favTab: 'vod',
    historyTab: 'vod',
    // 播放器增强状态
    danmakuEnabled: false,
    danmakuData: [],
    danmakuCanvas: null,
    danmakuCtx: null,
    danmakuTracks: [],
    danmakuOpacity: 0.7,
    danmakuSpeed: 1,
    danmakuFontSize: 20,
    danmakuLastTime: 0,
    danmakuAnimId: null,
    subtitleData: [],
    subtitleIndex: -1,
    aspectRatio: 'default',
    aspectRatios: ['default', '16:9', '4:3', 'stretch', 'original'],
    aspectRatioIndex: 0,
    isSeeking: false,
    controlsHideTimer: null,
    lastMouseMove: 0,
    // TVBox 原版补全状态
    incognitoMode: false,
    siteStyles: {},
    autoSwitching: false,
    mediaStatusTimer: null,
    unlockedGroups: new Set(),

    // ======== 初始化 ========

    async init() {
        if (window.pywebview && window.pywebview.api) {
            this.api = window.pywebview.api;
        } else {
            await new Promise(resolve => {
                window.addEventListener('pywebviewready', () => {
                    this.api = window.pywebview.api;
                    resolve();
                });
            });
            // 超时保护
            setTimeout(() => {
                if (!this.api) {
                    this.toast('API 加载超时,请重启应用', 'error');
                }
            }, 5000);
        }

        this.bindNav();
        this.bindSettings();
        this.bindSearch();
        this.bindLive();
        this.bindFavorites();
        this.bindHistory();
        this.bindPlayerControls();
        this.bindKeyboard();
        this.bindTheme();
        this.bindDensity();
        this.bindDownloads();

        // 恢复配置
        const savedUrl = await this.call('get_setting', 'configUrl', '');
        if (savedUrl) {
            document.getElementById('config-url').value = savedUrl;
            await this.loadConfig(savedUrl);
        }

        // 恢复直播源
        const savedLive = await this.call('get_setting', 'liveUrl', '');
        if (savedLive) {
            document.getElementById('live-url').value = savedLive;
        }

        // 恢复 EPG
        const savedEpg = await this.call('get_setting', 'epgUrl', '');
        if (savedEpg) {
            document.getElementById('epg-url').value = savedEpg;
        }

        // 恢复速度设置
        const savedSpeed = await this.call('get_setting', 'playSpeed', '1');
        const speedSelect = document.getElementById('default-speed');
        if (speedSelect) speedSelect.value = savedSpeed;

        // 恢复自动连播设置
        const autoNextVal = await this.call('get_setting', 'autoNext', '1');
        this.autoNext = autoNextVal === '1';
        const autoNextToggle = document.getElementById('auto-next-toggle');
        if (autoNextToggle) autoNextToggle.checked = this.autoNext;
        this.updateAutoNextBtn();

        // 恢复进度记忆设置
        const resumeVal = await this.call('get_setting', 'resumePlay', '1');
        this.resumePlay = resumeVal === '1';
        const resumeToggle = document.getElementById('resume-toggle');
        if (resumeToggle) resumeToggle.checked = this.resumePlay;

        // 恢复主题
        const savedTheme = await this.call('get_setting', 'theme', 'dark');
        this.applyTheme(savedTheme);
        const themeSelect = document.getElementById('theme-select');
        if (themeSelect) themeSelect.value = savedTheme;

        // 恢复密度
        const savedDensity = await this.call('get_setting', 'gridDensity', 'normal');
        this.applyDensity(savedDensity);

        // 加载已保存的配置列表
        await this.renderSavedConfigs();
        await this.renderSavedLives();
        await this.renderParses();
        await this.renderSearchHistory();

        // 启动下载进度刷新
        this.startDownloadTimer();

        // 加载壁纸
        await this.loadWallpaper();

        // 加载启动公告
        await this.loadNotice();

        // 恢复无痕模式状态
        await this.restoreIncognitoMode();

        // 启动远程控制状态上报
        this.startMediaStatusReport();
    },

    // ======== 壁纸系统 ========

    async loadWallpaper() {
        try {
            const result = await this.call('get_wallpaper');
            if (result && result.ok && result.url) {
                const url = result.url.trim();
                if (url) {
                    document.body.style.backgroundImage = `url("${url}")`;
                    document.body.style.backgroundSize = 'cover';
                    document.body.style.backgroundPosition = 'center';
                    document.body.style.backgroundAttachment = 'fixed';
                }
            }
        } catch (e) {
            console.error('加载壁纸失败:', e);
        }
    },

    // ======== 启动公告 ========

    async loadNotice() {
        try {
            const result = await this.call('get_notice');
            if (result && result.ok && result.notice && result.notice.trim()) {
                this.showNoticeModal(result.notice.trim());
            }
        } catch (e) {
            console.error('加载公告失败:', e);
        }
    },

    showNoticeModal(notice) {
        let modal = document.getElementById('notice-modal');
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'notice-modal';
            modal.className = 'modal';
            modal.innerHTML = `
                <div class="modal-backdrop"></div>
                <div class="modal-content" style="max-width:480px;padding:24px;">
                    <h2 style="margin-bottom:16px;">公告</h2>
                    <div id="notice-content" style="white-space:pre-wrap;line-height:1.6;margin-bottom:20px;max-height:60vh;overflow-y:auto;"></div>
                    <button id="notice-close-btn" class="btn-primary" style="width:100%;">我知道了</button>
                </div>
            `;
            document.body.appendChild(modal);
            modal.querySelector('.modal-backdrop').addEventListener('click', () => {
                modal.classList.add('hidden');
            });
        }
        document.getElementById('notice-content').textContent = notice;
        modal.classList.remove('hidden');
        const closeBtn = document.getElementById('notice-close-btn');
        closeBtn.onclick = () => modal.classList.add('hidden');
    },

    // ======== 无痕模式 ========

    async restoreIncognitoMode() {
        try {
            const result = await this.call('is_incognito');
            if (result && result.ok) {
                this.incognitoMode = result.enabled;
                const toggle = document.getElementById('incognito-toggle');
                if (toggle) toggle.checked = this.incognitoMode;
            }
        } catch (e) {
            console.error('恢复无痕模式状态失败:', e);
        }
    },

    async toggleIncognito(enabled) {
        try {
            const result = await this.call('set_incognito', enabled);
            if (result && result.ok) {
                this.incognitoMode = result.enabled;
                this.toast(this.incognitoMode ? '无痕模式已开启' : '无痕模式已关闭');
            }
        } catch (e) {
            this.toast('设置无痕模式失败', 'error');
        }
    },

    // ======== 远程控制状态上报 ========

    startMediaStatusReport() {
        if (this.mediaStatusTimer) clearInterval(this.mediaStatusTimer);
        this.mediaStatusTimer = setInterval(() => {
            this.reportMediaStatus();
        }, 15000);
    },

    async reportMediaStatus() {
        try {
            const video = document.getElementById('video-player');
            if (!video || !this.currentDetail) return;
            const status = {
                type: 'vod',
                vod_name: this.currentDetail.vod_name || '',
                site_key: this.currentSiteKey || '',
                position: Math.floor(video.currentTime || 0),
                duration: Math.floor(video.duration || 0),
                is_playing: !video.paused,
                volume: Math.round((video.volume || 0) * 100),
                ep_index: this.currentEpIndex,
                line_index: this.currentLine,
            };
            await this.call('set_media_status', JSON.stringify(status));
        } catch (e) {
            // 静默失败
        }
    },

    // ======== 导航 ========

    bindNav() {
        document.querySelectorAll('.nav-item').forEach(item => {
            item.addEventListener('click', () => {
                const view = item.dataset.view;
                this.switchView(view);
                if (view === 'favorites') this.loadFavorites();
                if (view === 'history') this.loadHistory();
                if (view === 'downloads') this.loadDownloads();
                if (view === 'live' && this.liveData) this.renderLiveGroups();
            });
        });
    },

    switchView(viewName) {
        document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
        const navItem = document.querySelector(`.nav-item[data-view="${viewName}"]`);
        if (navItem) navItem.classList.add('active');
        document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
        const view = document.getElementById(`view-${viewName}`);
        if (view) view.classList.add('active');
    },

    // ======== 工具 ========

    showLoading(text = '加载中...') {
        const el = document.getElementById('loading-text');
        if (el) el.textContent = text;
        document.getElementById('loading').classList.remove('hidden');
    },

    hideLoading() {
        document.getElementById('loading').classList.add('hidden');
    },

    toast(msg, type = '') {
        const t = document.createElement('div');
        t.className = `toast ${type}`;
        t.textContent = msg;
        document.body.appendChild(t);
        setTimeout(() => {
            t.style.opacity = '0';
            setTimeout(() => t.remove(), 300);
        }, 2500);
    },

    async call(fn, ...args) {
        try {
            return await this.api[fn](...args);
        } catch (e) {
            console.error(`API ${fn}:`, e);
            return { error: String(e) };
        }
    },

    placeholderImg() {
        return 'data:image/svg+xml,' + encodeURIComponent(
            '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="300"><rect width="200" height="300" fill="#242424"/><text x="100" y="150" text-anchor="middle" fill="#555" font-size="14">无图片</text></svg>'
        );
    },

    escape(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    },

    formatTime(ts) {
        if (!ts) return '';
        const d = new Date(ts * 1000);
        const now = new Date();
        const diff = (now - d) / 1000;
        if (diff < 60) return '刚刚';
        if (diff < 3600) return Math.floor(diff / 60) + '分钟前';
        if (diff < 86400) return Math.floor(diff / 3600) + '小时前';
        if (diff < 604800) return Math.floor(diff / 86400) + '天前';
        return `${d.getMonth() + 1}/${d.getDate()}`;
    },

    formatEpgTime(timeStr) {
        if (!timeStr || timeStr.length < 14) return timeStr || '';
        return timeStr.substring(8, 10) + ':' + timeStr.substring(10, 12);
    },

    formatDuration(seconds) {
        if (!seconds || seconds <= 0) return '00:00';
        const h = Math.floor(seconds / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        const s = Math.floor(seconds % 60);
        if (h > 0) {
            return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
        }
        return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
    },

    formatFileSize(bytes) {
        if (!bytes || bytes <= 0) return '0 B';
        const units = ['B', 'KB', 'MB', 'GB', 'TB'];
        let i = 0;
        while (bytes >= 1024 && i < units.length - 1) {
            bytes /= 1024;
            i++;
        }
        return bytes.toFixed(1) + ' ' + units[i];
    },

    // ======== 主题与密度 ========

    bindTheme() {
        const toggleBtn = document.getElementById('theme-toggle');
        if (toggleBtn) {
            toggleBtn.addEventListener('click', async () => {
                const current = document.documentElement.getAttribute('data-theme') || 'dark';
                const newTheme = current === 'dark' ? 'light' : 'dark';
                this.applyTheme(newTheme);
                await this.call('set_setting', 'theme', newTheme);
                const select = document.getElementById('theme-select');
                if (select) select.value = newTheme;
            });
        }

        const saveBtn = document.getElementById('save-theme-btn');
        if (saveBtn) {
            saveBtn.addEventListener('click', async () => {
                const theme = document.getElementById('theme-select').value;
                if (theme === 'auto') {
                    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
                    this.applyTheme(prefersDark ? 'dark' : 'light');
                } else {
                    this.applyTheme(theme);
                }
                await this.call('set_setting', 'theme', theme);
                this.toast('主题已保存', 'success');
            });
        }
    },

    applyTheme(theme) {
        if (theme === 'auto') {
            const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
            theme = prefersDark ? 'dark' : 'light';
        }
        if (theme === 'light') {
            document.documentElement.setAttribute('data-theme', 'light');
        } else {
            document.documentElement.removeAttribute('data-theme');
        }
    },

    bindDensity() {
        document.querySelectorAll('.density-btn').forEach(btn => {
            btn.addEventListener('click', async () => {
                document.querySelectorAll('.density-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                const density = btn.dataset.density;
                this.applyDensity(density);
                await this.call('set_setting', 'gridDensity', density);
            });
        });
    },

    applyDensity(density) {
        document.querySelectorAll('.vod-grid').forEach(grid => {
            grid.classList.remove('density-normal', 'density-compact', 'density-large');
            grid.classList.add('density-' + density);
        });
        document.querySelectorAll('.density-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.density === density);
        });
    },

    // ======== 配置管理 ========

    bindSettings() {
        document.getElementById('load-config-btn').addEventListener('click', async () => {
            const url = document.getElementById('config-url').value.trim();
            if (!url) return this.toast('请输入配置地址', 'error');
            await this.loadConfig(url);
        });

        document.getElementById('load-live-btn').addEventListener('click', async () => {
            const url = document.getElementById('live-url').value.trim();
            if (!url) return this.toast('请输入直播源地址', 'error');
            const type = parseInt(document.querySelector('input[name="live-type"]:checked').value);
            await this.loadLive(url, type);
        });

        document.getElementById('load-epg-btn').addEventListener('click', async () => {
            const url = document.getElementById('epg-url').value.trim();
            if (!url) return this.toast('请输入EPG地址', 'error');
            await this.call('set_setting', 'epgUrl', url);
            this.toast('EPG 地址已保存,加载直播源时自动加载', 'success');
        });

        document.getElementById('save-speed-btn').addEventListener('click', async () => {
            const speed = document.getElementById('default-speed').value;
            await this.call('set_setting', 'playSpeed', speed);
            this.toast('播放速度已保存', 'success');
        });

        const autoNextToggle = document.getElementById('auto-next-toggle');
        if (autoNextToggle) {
            autoNextToggle.addEventListener('change', async (e) => {
                this.autoNext = e.target.checked;
                await this.call('set_setting', 'autoNext', this.autoNext ? '1' : '0');
                this.updateAutoNextBtn();
                this.toast(this.autoNext ? '自动连播已开启' : '自动连播已关闭');
            });
        }

        const resumeToggle = document.getElementById('resume-toggle');
        if (resumeToggle) {
            resumeToggle.addEventListener('change', async (e) => {
                this.resumePlay = e.target.checked;
                await this.call('set_setting', 'resumePlay', this.resumePlay ? '1' : '0');
                this.toast(this.resumePlay ? '进度记忆已开启' : '进度记忆已关闭');
            });
        }

        // 无痕模式开关
        const incognitoToggle = document.getElementById('incognito-toggle');
        if (incognitoToggle) {
            incognitoToggle.addEventListener('change', async (e) => {
                await this.toggleIncognito(e.target.checked);
            });
        }

        // 嗅探规则管理
        document.getElementById('refresh-sniff-rules-btn')?.addEventListener('click', () => this.renderSniffRules());
        document.getElementById('add-sniff-rule-btn')?.addEventListener('click', () => this.addSniffRule());

        // 配置导入导出
        document.getElementById('export-config-btn')?.addEventListener('click', async () => {
            const result = await this.call('export_config');
            if (result.ok) {
                const blob = new Blob([result.data], { type: 'application/json' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `tvbox_backup_${new Date().toISOString().slice(0, 10)}.json`;
                a.click();
                URL.revokeObjectURL(url);
                this.toast('配置已导出', 'success');
            } else {
                this.toast('导出失败', 'error');
            }
        });

        document.getElementById('import-config-btn')?.addEventListener('click', () => {
            document.getElementById('import-config-file').click();
        });

        document.getElementById('import-config-file')?.addEventListener('change', async (e) => {
            const file = e.target.files[0];
            if (!file) return;
            const text = await file.text();
            const result = await this.call('import_config', text);
            if (result.ok) {
                this.toast('配置导入成功', 'success');
                await this.renderSavedConfigs();
                await this.renderSavedLives();
            } else {
                this.toast(result.error || '导入失败', 'error');
            }
            e.target.value = '';
        });
    },

    async loadConfig(url) {
        this.showLoading('加载配置中...');
        const result = await this.call('load_config', url);
        this.hideLoading();

        if (!result || !result.ok) {
            this.toast(result?.error || '加载失败', 'error');
            return;
        }

        await this.call('set_setting', 'configUrl', url);
        const s = result.summary;
        this.toast(`已加载 ${s.site_count} 个站点, ${s.live_count} 个直播源`, 'success');

        const badge = document.getElementById('config-status');
        badge.textContent = `已加载 ${s.site_count} 站点`;
        badge.classList.add('loaded');

        this.renderSites(s.sites);
        await this.renderSavedConfigs();
        await this.renderParses();
        await this.renderSiteManagement(s.sites);
        await this.renderSniffRules();

        // 保存站点样式配置
        this.siteStyles = {};
        if (s.sites) {
            s.sites.forEach(site => {
                if (site.style) {
                    this.siteStyles[site.key] = site.style;
                }
            });
        }

        if (s.lives && s.lives.length > 0) {
            document.getElementById('live-url').value = s.lives[0].url;
            if (s.lives[0].epg) {
                document.getElementById('epg-url').value = s.lives[0].epg;
            }
        }
    },

    async renderSavedConfigs() {
        const configs = await this.call('get_saved_configs');
        const container = document.getElementById('saved-configs');
        if (!container) return;
        container.innerHTML = '';

        if (!configs || configs.length === 0) {
            container.innerHTML = '<p class="form-hint">暂无保存的配置</p>';
            return;
        }

        configs.forEach(c => {
            const el = document.createElement('div');
            el.className = 'saved-item' + (c.is_active ? ' active' : '');
            el.innerHTML = `
                <div>
                    <div class="s-name">${this.escape(c.name)}</div>
                    <div class="s-url">${this.escape(c.url)}</div>
                </div>
                <div class="s-actions">
                    <button class="btn-sm" data-action="load" data-url="${this.escape(c.url)}">加载</button>
                    <button class="btn-sm btn-danger" data-action="del" data-id="${c.id}">删除</button>
                </div>
            `;
            container.appendChild(el);
        });

        container.querySelectorAll('[data-action="load"]').forEach(btn => {
            btn.addEventListener('click', () => {
                const url = btn.dataset.url;
                document.getElementById('config-url').value = url;
                this.loadConfig(url);
            });
        });
        container.querySelectorAll('[data-action="del"]').forEach(btn => {
            btn.addEventListener('click', async () => {
                await this.call('remove_config_url', parseInt(btn.dataset.id));
                this.renderSavedConfigs();
            });
        });
    },

    async renderSavedLives() {
        const lives = await this.call('get_saved_live_configs');
        const container = document.getElementById('saved-lives');
        if (!container) return;
        container.innerHTML = '';

        if (!lives || lives.length === 0) {
            container.innerHTML = '<p class="form-hint">暂无保存的直播源</p>';
            return;
        }

        lives.forEach(l => {
            const el = document.createElement('div');
            el.className = 'saved-item' + (l.is_active ? ' active' : '');
            el.innerHTML = `
                <div>
                    <div class="s-name">${this.escape(l.name)}</div>
                    <div class="s-url">${this.escape(l.url)}</div>
                </div>
                <div class="s-actions">
                    <button class="btn-sm" data-action="load" data-url="${this.escape(l.url)}" data-type="${l.source_type}">加载</button>
                    <button class="btn-sm btn-danger" data-action="del" data-id="${l.id}">删除</button>
                </div>
            `;
            container.appendChild(el);
        });

        container.querySelectorAll('[data-action="load"]').forEach(btn => {
            btn.addEventListener('click', () => {
                const url = btn.dataset.url;
                const type = parseInt(btn.dataset.type);
                document.getElementById('live-url').value = url;
                const radio = document.querySelector(`input[name="live-type"][value="${type}"]`);
                if (radio) radio.checked = true;
                this.loadLive(url, type);
            });
        });
        container.querySelectorAll('[data-action="del"]').forEach(btn => {
            btn.addEventListener('click', async () => {
                await this.call('remove_live_config', parseInt(btn.dataset.id));
                this.renderSavedLives();
            });
        });
    },

    async renderParses() {
        const parses = await this.call('get_parses');
        const container = document.getElementById('parse-list');
        if (!container) return;
        container.innerHTML = '';

        if (!parses || parses.length === 0) {
            container.innerHTML = '<p class="form-hint">当前配置无解析器</p>';
            return;
        }

        parses.forEach(p => {
            const el = document.createElement('div');
            el.className = 'parse-item';
            const typeText = p.type === 0 ? 'JSON接口' : p.type === 1 ? '嗅探' : 'JSON_V2';
            el.innerHTML = `
                <span>${this.escape(p.name)}</span>
                <span class="p-type">${typeText}</span>
            `;
            container.appendChild(el);
        });
    },

    async renderSiteManagement(sites) {
        const container = document.getElementById('site-management');
        if (!container) return;
        container.innerHTML = '';

        if (!sites || sites.length === 0) {
            container.innerHTML = '<p class="form-hint">加载配置后可管理站点</p>';
            return;
        }

        const list = document.createElement('div');
        list.className = 'site-mgmt-list';

        for (const site of sites) {
            const disabled = await this.call('get_setting', `site_disabled_${site.key}`, '0');
            const hidden = await this.call('get_setting', `site_hidden_${site.key}`, String(site.hide || 0));
            const item = document.createElement('div');
            item.className = 'site-mgmt-item' + (disabled === '1' ? ' disabled' : '') + (hidden === '1' ? ' hidden-site' : '');
            item.innerHTML = `
                <div class="sm-name">
                    <span class="sm-type type-${site.type}">${this.getSiteTypeLabel(site.type)}</span>
                    ${this.escape(site.name)}
                    ${hidden === '1' ? '<span style="font-size:11px;opacity:0.5;margin-left:4px;">(已隐藏)</span>' : ''}
                </div>
                <div class="sm-actions">
                    <button class="btn-sm sm-hide-btn" data-key="${site.key}" title="${hidden === '1' ? '显示' : '隐藏'}">${hidden === '1' ? '显示' : '隐藏'}</button>
                    <label class="switch-label">
                        <input type="checkbox" class="site-toggle" data-key="${site.key}" ${disabled !== '1' ? 'checked' : ''}>
                        <span class="switch-slider"></span>
                    </label>
                </div>
            `;
            list.appendChild(item);
        }

        container.appendChild(list);

        // 绑定开关事件 (启用/禁用)
        container.querySelectorAll('.site-toggle').forEach(toggle => {
            toggle.addEventListener('change', async (e) => {
                const key = e.target.dataset.key;
                const result = await this.call('toggle_site', key);
                const item = e.target.closest('.site-mgmt-item');
                item.classList.toggle('disabled', !e.target.checked);
                this.toast(e.target.checked ? '站点已启用' : '站点已禁用');
            });
        });

        // 绑定隐藏/显示切换事件
        container.querySelectorAll('.sm-hide-btn').forEach(btn => {
            btn.addEventListener('click', async (e) => {
                const key = e.target.dataset.key;
                const result = await this.call('toggle_site_hide', key);
                if (result && result.ok) {
                    this.toast(result.hidden ? '站点已隐藏' : '站点已显示');
                    // 重新渲染站点管理
                    this.renderSiteManagement(sites);
                    // 重新渲染首页站点选择器 (需重新获取站点)
                    const allSites = await this.call('get_sites');
                    // 合并 style 信息
                    if (allSites) {
                        allSites.forEach(s => {
                            const orig = sites.find(os => os.key === s.key);
                            if (orig) s.style = orig.style;
                        });
                        this.renderSites(allSites);
                    }
                } else {
                    this.toast(result?.error || '操作失败', 'error');
                }
            });
        });
    },

    getSiteTypeLabel(type) {
        const labels = { 0: 'API', 1: 'JAR', 3: 'PY', 4: 'JS' };
        return labels[type] || type;
    },

    // ======== 嗅探规则管理 ========

    async renderSniffRules() {
        const container = document.getElementById('sniff-rules-list');
        if (!container) return;
        container.innerHTML = '<p class="form-hint">加载中...</p>';

        try {
            const rules = await this.call('get_sniff_rules');
            container.innerHTML = '';

            if (!rules || rules.length === 0) {
                container.innerHTML = '<p class="form-hint">暂无嗅探规则</p>';
                return;
            }

            rules.forEach((rule, idx) => {
                const el = document.createElement('div');
                el.className = 'sniff-rule-item';
                const host = rule.host || '未指定';
                const regexList = (rule.regex || []).join(', ') || '无';
                const excludeList = (rule.exclude || []).join(', ') || '无';
                el.innerHTML = `
                    <div class="sr-info">
                        <div class="sr-host">${this.escape(host)}</div>
                        <div class="sr-detail" style="font-size:12px;opacity:0.7;">
                            正则: ${this.escape(regexList)} | 排除: ${this.escape(excludeList)}
                        </div>
                    </div>
                `;
                container.appendChild(el);
            });
        } catch (e) {
            container.innerHTML = '<p class="form-hint">加载嗅探规则失败</p>';
        }
    },

    async addSniffRule() {
        const host = prompt('请输入站点 Host (如: example.com):');
        if (!host) return;
        const regexStr = prompt('请输入嗅探正则 (多个用逗号分隔, 可留空):', '\\.mp4|\\.m3u8');
        const rule = { host: host.trim() };
        if (regexStr && regexStr.trim()) {
            rule.regex = regexStr.split(',').map(r => r.trim()).filter(r => r);
        }
        try {
            const result = await this.call('add_sniff_rule', JSON.stringify(rule));
            if (result && result.ok) {
                this.toast('嗅探规则已添加', 'success');
                this.renderSniffRules();
            } else {
                this.toast(result?.error || '添加失败', 'error');
            }
        } catch (e) {
            this.toast('添加嗅探规则失败', 'error');
        }
    },

    // ======== 首页 ========

    renderSites(sites) {
        const container = document.getElementById('site-selector');
        container.innerHTML = '';

        if (!sites || sites.length === 0) {
            container.innerHTML = '<span class="placeholder">配置中没有站点</span>';
            return;
        }

        // 过滤隐藏的站点 (hide=1 的站点不在首页显示)
        const visibleSites = sites.filter(s => !s.hide || s.hide === 0);
        if (visibleSites.length === 0) {
            container.innerHTML = '<span class="placeholder">所有站点已隐藏</span>';
            return;
        }

        visibleSites.forEach((site, idx) => {
            const chip = document.createElement('div');
            chip.className = 'site-chip' + (idx === 0 ? ' active' : '');
            chip.textContent = site.name;
            chip.addEventListener('click', () => {
                document.querySelectorAll('.site-chip').forEach(c => c.classList.remove('active'));
                chip.classList.add('active');
                this.loadHome(site.key);
            });
            container.appendChild(chip);
        });

        this.loadHome(visibleSites[0].key);
    },

    async loadHome(siteKey) {
        this.currentSiteKey = siteKey;
        this.showLoading('加载首页...');
        const data = await this.call('home_content', siteKey);
        this.hideLoading();

        if (data.error) {
            this.toast(data.error, 'error');
            document.getElementById('vod-grid').innerHTML =
                `<div class="empty-state"><p>${this.escape(data.error)}</p></div>`;
            return;
        }

        this.renderCategories(data.categories || []);
        this.currentFilters = data.filters || {};

        if (data.list && data.list.length > 0) {
            this.renderVodGrid(data.list);
        }

        if (data.categories && data.categories.length > 0) {
            this.loadCategory(data.categories[0].type_id, 1);
        }
    },

    renderCategories(categories) {
        const bar = document.getElementById('category-bar');
        bar.innerHTML = '';

        categories.forEach((cat, idx) => {
            const chip = document.createElement('div');
            chip.className = 'cat-chip' + (idx === 0 ? ' active' : '');
            chip.textContent = cat.type_name;
            chip.addEventListener('click', () => {
                document.querySelectorAll('.cat-chip').forEach(c => c.classList.remove('active'));
                chip.classList.add('active');
                this.renderFilters(cat.type_id);
                this.loadCategory(cat.type_id, 1);
            });
            bar.appendChild(chip);
        });

        if (categories.length > 0) {
            this.renderFilters(categories[0].type_id);
        }
    },

    renderFilters(tid) {
        const bar = document.getElementById('filter-bar');
        bar.innerHTML = '';

        const filters = this.currentFilters[tid] || this.currentFilters;
        if (!filters || (Array.isArray(filters) && filters.length === 0) ||
            (typeof filters === 'object' && Object.keys(filters).length === 0)) {
            bar.classList.remove('active');
            return;
        }

        bar.classList.add('active');

        let filterList = [];
        if (Array.isArray(filters)) {
            filterList = filters.map(f => ({
                key: f.key || f.k,
                name: f.name || f.n,
                values: f.value || f.v || []
            }));
        } else if (typeof filters === 'object') {
            for (const [k, vals] of Object.entries(filters)) {
                if (Array.isArray(vals)) {
                    const name = vals[0]?.name || vals[0]?.n || k;
                    filterList.push({ key: k, name: name, values: vals });
                }
            }
        }

        filterList.forEach(group => {
            const g = document.createElement('div');
            g.className = 'filter-group';
            g.innerHTML = `<span class="filter-label">${this.escape(group.name)}:</span>`;

            const allOption = document.createElement('div');
            allOption.className = 'filter-option active';
            allOption.textContent = '全部';
            allOption.dataset.value = '';
            allOption.addEventListener('click', () => {
                g.querySelectorAll('.filter-option').forEach(o => o.classList.remove('active'));
                allOption.classList.add('active');
                this.applyFilters();
            });
            g.appendChild(allOption);

            (group.values || []).forEach(v => {
                const opt = document.createElement('div');
                opt.className = 'filter-option';
                opt.textContent = v.n || v.name || v.v || v.value;
                opt.dataset.value = v.v || v.value;
                opt.dataset.key = group.key;
                opt.addEventListener('click', () => {
                    g.querySelectorAll('.filter-option').forEach(o => o.classList.remove('active'));
                    opt.classList.add('active');
                    this.applyFilters();
                });
                g.appendChild(opt);
            });

            bar.appendChild(g);
        });
    },

    applyFilters() {
        const bar = document.getElementById('filter-bar');
        const extend = {};
        bar.querySelectorAll('.filter-option.active[data-value]').forEach(opt => {
            if (opt.dataset.value) {
                extend[opt.dataset.key] = opt.dataset.value;
            }
        });
        if (Object.keys(extend).length > 0) {
            this.loadCategory(this.currentCategory, 1, JSON.stringify(extend));
        } else {
            this.loadCategory(this.currentCategory, 1);
        }
    },

    async loadCategory(tid, pg, extend) {
        this.currentCategory = tid;
        this.currentPage = pg;
        this.showLoading('加载内容...');
        const data = await this.call('category_content', this.currentSiteKey, tid, pg, extend || '');
        this.hideLoading();

        if (data.error) {
            this.toast(data.error, 'error');
            return;
        }

        this.renderVodGrid(data.list || []);
        this.renderPagination(data.page || 1, data.pagecount || 1);
    },

    renderVodGrid(items) {
        const grid = document.getElementById('vod-grid');
        grid.innerHTML = '';

        if (!items || items.length === 0) {
            grid.innerHTML = '<div class="empty-state"><p>暂无内容</p></div>';
            return;
        }

        // 获取当前站点样式
        const style = this.siteStyles[this.currentSiteKey] || { type: 'rect', ratio: 1.78 };
        const cardType = style.type || 'rect';
        const ratio = parseFloat(style.ratio) || 1.78;

        // 根据样式类型设置 grid 布局
        grid.classList.remove('card-rect', 'card-oval', 'card-list');
        grid.classList.add('card-' + cardType);

        items.forEach(item => {
            const card = document.createElement('div');
            card.className = 'vod-card card-' + cardType;
            // 根据宽高比设置海报比例
            const posterStyle = ratio > 0
                ? `style="aspect-ratio:${ratio};"`
                : '';

            if (cardType === 'list') {
                // 列表样式: 横向排列
                card.innerHTML = `
                    <div class="poster list-poster" ${posterStyle}>
                        <img src="${item.vod_pic || this.placeholderImg()}" onerror="this.src='${this.placeholderImg()}'" loading="lazy">
                        ${item.vod_remarks ? `<span class="remarks">${this.escape(item.vod_remarks)}</span>` : ''}
                    </div>
                    <div class="vod-list-info">
                        <div class="vod-name">${this.escape(item.vod_name)}</div>
                        <div class="vod-sub">${item.vod_year || ''} ${item.type_name || ''}</div>
                    </div>
                `;
            } else {
                // rect / oval 样式
                const posterClass = cardType === 'oval' ? 'poster oval-poster' : 'poster';
                card.innerHTML = `
                    <div class="${posterClass}" ${posterStyle}>
                        <img src="${item.vod_pic || this.placeholderImg()}" onerror="this.src='${this.placeholderImg()}'" loading="lazy">
                        ${item.vod_remarks ? `<span class="remarks">${this.escape(item.vod_remarks)}</span>` : ''}
                    </div>
                    <div class="vod-name">${this.escape(item.vod_name)}</div>
                    <div class="vod-sub">${item.vod_year || ''} ${item.type_name || ''}</div>
                `;
            }
            card.addEventListener('click', () => {
                this.showDetail(item.site_key || this.currentSiteKey, item.vod_id);
            });
            grid.appendChild(card);
        });
    },

    renderPagination(page, pagecount) {
        const pag = document.getElementById('pagination');
        pag.innerHTML = '';
        if (pagecount <= 1) return;

        const prev = document.createElement('button');
        prev.className = 'page-btn';
        prev.textContent = '上一页';
        prev.disabled = page <= 1;
        prev.addEventListener('click', () => this.loadCategory(this.currentCategory, page - 1));

        const info = document.createElement('span');
        info.className = 'page-info';
        info.textContent = `${page} / ${pagecount}`;

        const next = document.createElement('button');
        next.className = 'page-btn';
        next.textContent = '下一页';
        next.disabled = page >= pagecount;
        next.addEventListener('click', () => this.loadCategory(this.currentCategory, page + 1));

        pag.appendChild(prev);
        pag.appendChild(info);
        pag.appendChild(next);
    },

    // ======== 搜索 ========

    bindSearch() {
        const input = document.getElementById('search-input');
        const btn = document.getElementById('search-btn');

        btn.addEventListener('click', () => this.doSearch());

        input.addEventListener('keypress', e => {
            if (e.key === 'Enter') {
                this.doSearch();
                document.getElementById('search-suggestions').classList.add('hidden');
            }
        });

        // 搜索建议
        let suggestTimer = null;
        input.addEventListener('input', () => {
            if (suggestTimer) clearTimeout(suggestTimer);
            suggestTimer = setTimeout(() => {
                this.showSearchSuggestions(input.value.trim());
            }, 300);
        });

        input.addEventListener('focus', () => {
            if (input.value.trim()) {
                this.showSearchSuggestions(input.value.trim());
            }
        });

        // 点击外部关闭建议
        document.addEventListener('click', (e) => {
            if (!e.target.closest('.search-input-wrapper')) {
                document.getElementById('search-suggestions').classList.add('hidden');
            }
        });
    },

    async showSearchSuggestions(prefix) {
        const container = document.getElementById('search-suggestions');
        if (!prefix) {
            container.classList.add('hidden');
            return;
        }

        const suggestions = await this.call('get_search_suggestions', prefix, 8);
        if (!suggestions || suggestions.length === 0) {
            container.classList.add('hidden');
            return;
        }

        container.innerHTML = '';
        suggestions.forEach(s => {
            const item = document.createElement('div');
            item.className = 'suggestion-item';
            item.innerHTML = `
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
                <span>${this.escape(s)}</span>
            `;
            item.addEventListener('click', () => {
                document.getElementById('search-input').value = s;
                container.classList.add('hidden');
                this.doSearch();
            });
            container.appendChild(item);
        });
        container.classList.remove('hidden');
    },

    async renderSearchHistory() {
        const container = document.getElementById('search-history-bar');
        if (!container) return;

        const history = await this.call('get_search_history', 15);
        if (!history || history.length === 0) {
            container.innerHTML = '';
            container.classList.remove('active');
            return;
        }

        container.classList.add('active');
        let html = '<span class="search-history-label">搜索历史:</span>';
        history.forEach(h => {
            html += `<div class="search-history-tag" data-keyword="${this.escape(h.keyword)}">${this.escape(h.keyword)}</div>`;
        });
        html += '<div class="search-history-clear" id="clear-search-history">清空</div>';
        container.innerHTML = html;

        container.querySelectorAll('.search-history-tag').forEach(tag => {
            tag.addEventListener('click', () => {
                document.getElementById('search-input').value = tag.dataset.keyword;
                this.doSearch();
            });
        });

        document.getElementById('clear-search-history')?.addEventListener('click', async () => {
            await this.call('clear_search_history');
            this.renderSearchHistory();
            this.toast('搜索历史已清空');
        });
    },

    async doSearch() {
        const keyword = document.getElementById('search-input').value.trim();
        if (!keyword) return;

        // 繁转简: 搜索前转换关键词
        let searchKeyword = keyword;
        try {
            const t2sResult = await this.call('t2s', keyword);
            if (t2sResult && t2sResult.ok && t2sResult.text && t2sResult.text !== keyword) {
                searchKeyword = t2sResult.text;
            }
        } catch (e) {
            // 转换失败则使用原关键词
        }

        this.showLoading('搜索中...');
        let results = await this.call('search_all', searchKeyword);
        this.hideLoading();

        // 刷新搜索历史
        this.renderSearchHistory();

        const grid = document.getElementById('search-results');
        const tabs = document.getElementById('search-site-tabs');

        if (!results || results.length === 0) {
            // 如果使用了转换后的关键词且无结果, 尝试用原始关键词搜索
            if (searchKeyword !== keyword) {
                this.showLoading('尝试用原关键词搜索...');
                results = await this.call('search_all', keyword);
                this.hideLoading();
            }
            if (!results || results.length === 0) {
                tabs.innerHTML = '';
                grid.innerHTML = '<div class="empty-state"><p>未找到结果</p></div>';
                return;
            }
        }

        tabs.innerHTML = '';
        grid.innerHTML = '';

        results.forEach((group, idx) => {
            const tab = document.createElement('div');
            tab.className = 'site-tab' + (idx === 0 ? ' active' : '');
            tab.textContent = `${group.site_name} (${group.list.length})`;
            tab.addEventListener('click', () => {
                document.querySelectorAll('.site-tab').forEach(t => t.classList.remove('active'));
                tab.classList.add('active');
                this.renderSearchGroup(group, grid);
            });
            tabs.appendChild(tab);
        });

        this.renderSearchGroup(results[0], grid);
    },

    renderSearchGroup(group, container) {
        container.innerHTML = '';
        (group.list || []).forEach(item => {
            const card = document.createElement('div');
            card.className = 'vod-card';
            card.innerHTML = `
                <div class="poster">
                    <img src="${item.vod_pic || this.placeholderImg()}" onerror="this.src='${this.placeholderImg()}'" loading="lazy">
                    ${item.vod_remarks ? `<span class="remarks">${this.escape(item.vod_remarks)}</span>` : ''}
                </div>
                <div class="vod-name">${this.escape(item.vod_name)}</div>
                <div class="vod-sub">${item.vod_year || ''} ${item.site_name || ''}</div>
            `;
            card.addEventListener('click', () => {
                this.showDetail(item.site_key || group.site_key, item.vod_id);
            });
            container.appendChild(card);
        });
    },

    // ======== 详情 ========

    async showDetail(siteKey, vodId) {
        this.showLoading('加载详情...');
        const data = await this.call('detail_content', siteKey, vodId);
        this.hideLoading();

        if (data.error) {
            this.toast(data.error, 'error');
            return;
        }

        const items = data.list || [];
        if (items.length === 0) {
            this.toast('未找到详情', 'error');
            return;
        }

        const vod = items[0];
        this.currentSiteKey = siteKey;
        this.currentDetail = vod;

        // 集数解析增强: 优先使用后端 parse_episodes (支持多线路 + <300集倒序)
        let episodes = null;
        try {
            const parsed = await this.call('parse_episodes', vod.vod_play_from || '', vod.vod_play_url || '');
            if (parsed && parsed.ok && parsed.lines && parsed.lines.length > 0) {
                episodes = parsed.lines;
            }
        } catch (e) {
            // 后端解析失败, 回退到前端解析
        }
        this.currentEpisodes = episodes || this.parsePlayUrl(vod.vod_play_from, vod.vod_play_url);

        // 检查是否有播放历史
        const history = await this.call('get_history_item', vod.vod_id, siteKey);
        if (history && history.episode_index !== undefined) {
            this.currentEpIndex = history.episode_index;
            this.currentLine = history.line_index || 0;
        } else {
            this.currentEpIndex = 0;
            this.currentLine = 0;
        }

        // 检查收藏状态
        const favStatus = await this.call('is_favorite', vod.vod_id, siteKey);

        this.renderDetail(vod, favStatus.is_favorite);
        document.getElementById('detail-modal').classList.remove('hidden');
    },

    parsePlayUrl(playFrom, playUrl) {
        const lines = [];
        if (!playFrom || !playUrl) return lines;

        const fromList = playFrom.split('$$$');
        const urlList = playUrl.split('$$$');

        for (let i = 0; i < fromList.length; i++) {
            const lineName = fromList[i].trim();
            const urlStr = (urlList[i] || '').trim();
            const episodes = [];

            if (urlStr) {
                const eps = urlStr.split('#');
                for (const ep of eps) {
                    const parts = ep.split('$');
                    if (parts.length >= 2) {
                        episodes.push({ name: parts[0].trim(), url: parts[1].trim() });
                    } else if (parts.length === 1 && parts[0].trim()) {
                        episodes.push({ name: `第${episodes.length + 1}集`, url: parts[0].trim() });
                    }
                }
            }
            lines.push({ name: lineName, episodes });
        }
        return lines;
    },

    async renderDetail(vod, isFav) {
        const body = document.getElementById('detail-body');

        let metaHtml = '';
        if (vod.vod_year) metaHtml += `<span>${vod.vod_year}</span>`;
        if (vod.vod_area) metaHtml += `<span>${vod.vod_area}</span>`;
        if (vod.type_name) metaHtml += `<span>${this.escape(vod.type_name)}</span>`;
        if (vod.vod_score) metaHtml += `<span>评分: ${this.escape(vod.vod_score)}</span>`;

        let textHtml = '';
        if (vod.vod_director) textHtml += `<p class="detail-text"><strong>导演:</strong> ${this.escape(vod.vod_director)}</p>`;
        if (vod.vod_actor) textHtml += `<p class="detail-text"><strong>演员:</strong> ${this.escape(vod.vod_actor)}</p>`;
        if (vod.vod_content) textHtml += `<p class="detail-text"><strong>简介:</strong> ${this.escape(vod.vod_content)}</p>`;

        let linesHtml = '<div class="episode-lines">';
        this.currentEpisodes.forEach((line, idx) => {
            linesHtml += `<div class="episode-line${idx === this.currentLine ? ' active' : ''}" data-line="${idx}">${this.escape(line.name)}</div>`;
        });
        linesHtml += '</div>';

        let epsHtml = '<div class="episode-list" id="episode-list"></div>';

        const favText = isFav ? '已收藏' : '收藏';
        const favClass = isFav ? 'fav-btn favorited' : 'fav-btn';

        body.innerHTML = `
            <div class="detail-poster">
                <img src="${vod.vod_pic || this.placeholderImg()}" onerror="this.src='${this.placeholderImg()}'">
                <button id="fav-btn" class="${favClass}" style="margin-top:10px;width:100%">${favText}</button>
            </div>
            <div class="detail-info">
                <h2>${this.escape(vod.vod_name)}</h2>
                <div class="detail-meta">${metaHtml}</div>
                ${textHtml}
                <div class="detail-episodes">
                    <h3 style="font-size:14px;margin-bottom:10px;color:var(--text-secondary)">播放线路</h3>
                    ${linesHtml}
                    <h3 style="font-size:14px;margin-bottom:10px;margin-top:12px;color:var(--text-secondary)">选集</h3>
                    ${epsHtml}
                </div>
            </div>
        `;

        // 收藏按钮
        const favBtn = document.getElementById('fav-btn');
        favBtn.addEventListener('click', async () => {
            const site = await this.call('get_sites');
            const siteInfo = site.find(s => s.key === this.currentSiteKey);
            if (favBtn.classList.contains('favorited')) {
                await this.call('remove_favorite', vod.vod_id, this.currentSiteKey);
                favBtn.classList.remove('favorited');
                favBtn.textContent = '收藏';
                this.toast('已取消收藏');
            } else {
                await this.call('add_favorite', vod.vod_id, vod.vod_name, vod.vod_pic,
                    this.currentSiteKey, siteInfo?.name || '', vod.vod_remarks || '');
                favBtn.classList.add('favorited');
                favBtn.textContent = '已收藏';
                this.toast('已收藏', 'success');
            }
        });

        // 线路切换
        body.querySelectorAll('.episode-line').forEach(el => {
            el.addEventListener('click', () => {
                body.querySelectorAll('.episode-line').forEach(e => e.classList.remove('active'));
                el.classList.add('active');
                this.currentLine = parseInt(el.dataset.line);
                this.currentEpIndex = 0;
                this.renderEpisodes();
            });
        });

        this.renderEpisodes();
    },

    renderEpisodes() {
        const list = document.getElementById('episode-list');
        if (!list) return;

        const line = this.currentEpisodes[this.currentLine];
        if (!line) return;

        list.innerHTML = '';
        line.episodes.forEach((ep, idx) => {
            const el = document.createElement('div');
            el.className = 'episode-item' + (idx === this.currentEpIndex ? ' playing' : '');
            el.textContent = ep.name;
            el.addEventListener('click', () => {
                this.currentEpIndex = idx;
                this.playVideo(this.currentSiteKey, line.name, ep.url, idx);
            });
            list.appendChild(el);
        });
    },

    closeDetail() {
        document.getElementById('detail-modal').classList.add('hidden');
    },

    // ======== 播放器 ========

    bindPlayerControls() {
        const video = document.getElementById('video-player');
        const volumeSlider = document.getElementById('volume-slider');
        const progressBar = document.getElementById('player-progress-bar');
        const wrapper = document.getElementById('player-wrapper');

        // 视频时间更新 -> 更新自定义进度条
        video.addEventListener('timeupdate', () => {
            if (!this.isSeeking) this.updateProgressBar();
            this.updatePlayerTime();
            this.updateSubtitle();
            this.updateDanmaku();
        });

        // 视频加载元数据
        video.addEventListener('loadedmetadata', () => {
            this.updatePlayerTime();
            this.updateProgressBar();
            this.initDanmakuCanvas();
        });

        // 缓冲进度
        video.addEventListener('progress', () => {
            this.updateBuffered();
        });

        // 缓冲中
        video.addEventListener('waiting', () => {
            document.getElementById('player-buffering')?.classList.remove('hidden');
        });
        video.addEventListener('playing', () => {
            document.getElementById('player-buffering')?.classList.add('hidden');
        });
        video.addEventListener('canplay', () => {
            document.getElementById('player-buffering')?.classList.add('hidden');
        });

        // 视频结束 - 自动连播
        video.addEventListener('ended', () => {
            if (this.autoNext) {
                this.nextEpisode();
            }
        });

        // 播放/暂停状态
        video.addEventListener('play', () => {
            this.updatePlayButton(true);
            this.startDanmakuAnimation();
        });
        video.addEventListener('pause', () => {
            this.updatePlayButton(false);
            this.stopDanmakuAnimation();
        });

        // 音量滑块
        if (volumeSlider) {
            volumeSlider.addEventListener('input', () => {
                const vol = parseInt(volumeSlider.value);
                video.volume = vol / 100;
                if (vol === 0) {
                    this.isMuted = true;
                } else {
                    this.isMuted = false;
                    this.lastVolume = vol;
                }
                this.updateMuteButton();
            });
        }

        // ======== 自定义进度条 ========
        if (progressBar) {
            let isDragging = false;

            const seekToPosition = (clientX) => {
                const rect = progressBar.getBoundingClientRect();
                const pct = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
                if (video.duration) {
                    video.currentTime = pct * video.duration;
                }
                document.getElementById('progress-played').style.width = (pct * 100) + '%';
                document.getElementById('progress-thumb').style.left = (pct * 100) + '%';
            };

            progressBar.addEventListener('mousedown', (e) => {
                isDragging = true;
                this.isSeeking = true;
                seekToPosition(e.clientX);
            });

            document.addEventListener('mousemove', (e) => {
                if (isDragging) {
                    seekToPosition(e.clientX);
                }
            });

            document.addEventListener('mouseup', () => {
                if (isDragging) {
                    isDragging = false;
                    this.isSeeking = false;
                }
            });

            // 预览时间
            progressBar.addEventListener('mousemove', (e) => {
                const rect = progressBar.getBoundingClientRect();
                const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
                if (video.duration) {
                    const time = this.formatDuration(pct * video.duration);
                    progressBar.title = time;
                }
            });
        }

        // ======== 双击全屏 ========
        let clickTimer = null;
        video.addEventListener('click', () => {
            if (clickTimer) {
                clearTimeout(clickTimer);
                clickTimer = null;
                this.toggleFullscreen();
            } else {
                clickTimer = setTimeout(() => {
                    clickTimer = null;
                    this.togglePlay();
                }, 250);
            }
        });

        // ======== 滚轮调节音量 ========
        wrapper?.addEventListener('wheel', (e) => {
            e.preventDefault();
            const delta = e.deltaY > 0 ? -5 : 5;
            const slider = document.getElementById('volume-slider');
            const newVol = Math.max(0, Math.min(100, parseInt(slider.value) + delta));
            slider.value = newVol;
            video.volume = newVol / 100;
            if (newVol > 0) this.isMuted = false;
            this.updateMuteButton();
            this.showVideoInfo(`音量 ${newVol}%`);
        }, { passive: false });

        // ======== 鼠标移动显示/隐藏控制条 ========
        wrapper?.addEventListener('mousemove', () => {
            this.showPlayerControls();
            this.lastMouseMove = Date.now();
            clearTimeout(this.controlsHideTimer);
            this.controlsHideTimer = setTimeout(() => {
                if (!video.paused && Date.now() - this.lastMouseMove >= 2800) {
                    this.hidePlayerControls();
                }
            }, 3000);
        });

        wrapper?.addEventListener('mouseleave', () => {
            if (!video.paused) {
                this.hidePlayerControls();
            }
        });

        // ======== 右键菜单 ========
        video.addEventListener('contextmenu', (e) => {
            e.preventDefault();
            this.showPlayerContextMenu(e.clientX, e.clientY);
        });

        document.addEventListener('click', (e) => {
            if (!e.target.closest('#player-context-menu')) {
                document.getElementById('player-context-menu')?.classList.add('hidden');
            }
        });

        // 右键菜单事件
        document.querySelectorAll('#player-context-menu .pcm-item[data-action]').forEach(item => {
            item.addEventListener('click', (e) => {
                e.stopPropagation();
                const action = item.dataset.action;
                const val = item.dataset.val;
                this.handlePlayerContextMenu(action, val);
                document.getElementById('player-context-menu').classList.add('hidden');
            });
        });

        // ======== 弹幕设置弹窗 ========
        const searchDanmakuBtn = document.getElementById('search-danmaku-btn');
        if (searchDanmakuBtn) {
            searchDanmakuBtn.addEventListener('click', () => {
                this.searchDanmaku();
            });
        }
        // 打开弹幕设置时自动加载弹幕API配置
        const danmakuSettingsModal = document.getElementById('danmaku-settings-modal');
        if (danmakuSettingsModal) {
            const observer = new MutationObserver(() => {
                if (!danmakuSettingsModal.classList.contains('hidden')) {
                    this.loadDanmakuApi();
                }
            });
            observer.observe(danmakuSettingsModal, { attributes: true, attributeFilter: ['class'] });
        }
        const danmakuOpacity = document.getElementById('danmaku-opacity');
        if (danmakuOpacity) {
            danmakuOpacity.addEventListener('input', (e) => {
                this.danmakuOpacity = e.target.value / 100;
            });
        }
        const danmakuSpeed = document.getElementById('danmaku-speed');
        if (danmakuSpeed) {
            danmakuSpeed.addEventListener('change', (e) => {
                this.danmakuSpeed = e.target.value === 'slow' ? 0.5 : e.target.value === 'fast' ? 2 : 1;
            });
        }
        const danmakuSize = document.getElementById('danmaku-size');
        if (danmakuSize) {
            danmakuSize.addEventListener('change', (e) => {
                this.danmakuFontSize = e.target.value === 'small' ? 16 : e.target.value === 'large' ? 28 : 20;
            });
        }
    },

    // ======== 自定义进度条 ========

    updateProgressBar() {
        const video = document.getElementById('video-player');
        if (!video || !video.duration) return;
        const pct = (video.currentTime / video.duration) * 100;
        document.getElementById('progress-played').style.width = pct + '%';
        document.getElementById('progress-thumb').style.left = pct + '%';
    },

    updateBuffered() {
        const video = document.getElementById('video-player');
        if (!video || !video.buffered.length) return;
        const buffered = video.buffered.end(video.buffered.length - 1);
        const pct = (buffered / (video.duration || 1)) * 100;
        document.getElementById('progress-buffered').style.width = pct + '%';
    },

    // ======== 播放器控制条显示/隐藏 ========

    showPlayerControls() {
        const wrapper = document.getElementById('player-wrapper');
        if (wrapper) wrapper.classList.remove('controls-hidden');
    },

    hidePlayerControls() {
        const video = document.getElementById('video-player');
        if (video && !video.paused) {
            const wrapper = document.getElementById('player-wrapper');
            if (wrapper) wrapper.classList.add('controls-hidden');
        }
    },

    // ======== 视频信息覆盖层 ========

    showVideoInfo(text, duration = 1500) {
        const overlay = document.getElementById('video-info-overlay');
        const textEl = document.getElementById('video-info-text');
        if (!overlay || !textEl) return;
        textEl.textContent = text;
        overlay.classList.remove('hidden');
        clearTimeout(this._infoTimer);
        this._infoTimer = setTimeout(() => {
            overlay.classList.add('hidden');
        }, duration);
    },

    // ======== 右键菜单 ========

    showPlayerContextMenu(x, y) {
        const menu = document.getElementById('player-context-menu');
        const modal = document.getElementById('player-modal');
        const rect = modal.getBoundingClientRect();
        menu.style.left = (x - rect.left) + 'px';
        menu.style.top = (y - rect.top) + 'px';
        menu.classList.remove('hidden');
    },

    handlePlayerContextMenu(action, val) {
        switch (action) {
            case 'screenshot':
                this.screenshot();
                break;
            case 'subtitle':
                this.loadSubtitle();
                break;
            case 'danmaku':
                document.getElementById('danmaku-settings-modal').classList.remove('hidden');
                break;
            case 'ratio':
                this.setAspectRatio(val);
                break;
            case 'speed':
                this.setSpeed(parseFloat(val));
                break;
            case 'external':
                this.openExternalPlayer();
                break;
            case 'copy-url':
                this.copyPlayUrl();
                break;
        }
    },

    // ======== 画面比例 ========

    toggleAspectRatio() {
        this.aspectRatioIndex = (this.aspectRatioIndex + 1) % this.aspectRatios.length;
        this.setAspectRatio(this.aspectRatios[this.aspectRatioIndex]);
    },

    setAspectRatio(ratio) {
        this.aspectRatio = ratio;
        this.aspectRatioIndex = this.aspectRatios.indexOf(ratio);
        const video = document.getElementById('video-player');
        if (!video) return;

        const ratios = {
            'default': '',
            '16:9': '16 / 9',
            '4:3': '4 / 3',
            'stretch': 'fill',
            'original': 'contain',
        };

        if (ratio === 'stretch') {
            video.style.objectFit = 'fill';
            video.style.aspectRatio = '';
        } else if (ratio === 'original') {
            video.style.objectFit = 'contain';
            video.style.aspectRatio = '';
        } else if (ratio === 'default') {
            video.style.objectFit = '';
            video.style.aspectRatio = '';
        } else {
            video.style.objectFit = '';
            video.style.aspectRatio = ratios[ratio];
        }

        const labels = { 'default': '默认', '16:9': '16:9', '4:3': '4:3', 'stretch': '拉伸', 'original': '原始' };
        this.showVideoInfo(`画面比例: ${labels[ratio] || ratio}`);
    },

    // ======== 截图 ========

    screenshot() {
        const video = document.getElementById('video-player');
        if (!video || !video.videoWidth) {
            this.toast('无法截图,视频未加载', 'error');
            return;
        }

        const canvas = document.createElement('canvas');
        canvas.width = video.videoWidth;
        canvas.height = video.videoHeight;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(video, 0, 0);

        const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
        const name = this.currentDetail?.vod_name || 'screenshot';

        canvas.toBlob(async (blob) => {
            if (!blob) {
                this.toast('截图失败', 'error');
                return;
            }
            // 转为 base64 发送给 Python 保存
            const reader = new FileReader();
            reader.onload = async () => {
                const base64 = reader.result.split(',')[1];
                const result = await this.call('save_screenshot', name, timestamp, base64);
                if (result.ok) {
                    this.toast(`截图已保存: ${result.path}`, 'success');
                } else {
                    this.toast('截图保存失败', 'error');
                }
            };
            reader.readAsDataURL(blob);
        }, 'image/png');
    },

    // ======== 字幕 ========

    async loadSubtitle() {
        // 触发文件选择
        const input = document.createElement('input');
        input.type = 'file';
        input.accept = '.srt,.vtt,.ass,.ssa';
        input.onchange = async (e) => {
            const file = e.target.files[0];
            if (!file) return;

            const text = await file.text();
            this.subtitleData = this.parseSubtitle(text, file.name);
            this.subtitleIndex = -1;

            if (this.subtitleData.length > 0) {
                this.toast(`已加载字幕: ${file.name} (${this.subtitleData.length}条)`, 'success');
            } else {
                this.toast('字幕解析失败', 'error');
            }
        };
        input.click();
    },

    parseSubtitle(text, filename) {
        const subtitles = [];
        const ext = filename.split('.').pop().toLowerCase();

        if (ext === 'vtt' || ext === 'srt') {
            // SRT/VTT 格式
            const lines = text.replace(/\r\n/g, '\n').split('\n');
            let i = 0;
            while (i < lines.length) {
                const line = lines[i].trim();
                // 跳过序号或空行
                if (/^\d+$/.test(line) || line === '' || line.startsWith('WEBVTT')) {
                    i++;
                    continue;
                }
                // 时间轴行
                const timeMatch = line.match(/(\d{2}:\d{2}:\d{2}[.,]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[.,]\d{3})/);
                if (timeMatch) {
                    const start = this.parseTimecode(timeMatch[1]);
                    const end = this.parseTimecode(timeMatch[2]);
                    i++;
                    let content = '';
                    while (i < lines.length && lines[i].trim() !== '' && !/^\d+$/.test(lines[i].trim())) {
                        content += (content ? '\n' : '') + lines[i].trim();
                        i++;
                    }
                    if (content) {
                        subtitles.push({ start, end, text: content });
                    }
                } else {
                    i++;
                }
            }
        } else if (ext === 'ass' || ext === 'ssa') {
            // ASS/SSA 格式 (简化解析)
            const dialogueMatch = text.match(/Format:\s*(.*)/i);
            const eventsSection = text.split(/\[Events\]/i)[1];
            if (eventsSection) {
                const lines = eventsSection.split('\n');
                lines.forEach(line => {
                    if (line.trim().startsWith('Dialogue:')) {
                        const parts = line.trim().substring(9).split(',');
                        if (parts.length >= 10) {
                            const start = this.parseAssTime(parts[1].trim());
                            const end = this.parseAssTime(parts[2].trim());
                            const textContent = parts.slice(9).join(',').replace(/\{[^}]*\}/g, '').replace(/\\N/g, '\n');
                            subtitles.push({ start, end, text: textContent });
                        }
                    }
                });
            }
        }

        subtitles.sort((a, b) => a.start - b.start);
        return subtitles;
    },

    parseTimecode(tc) {
        const parts = tc.replace(',', '.').split(':');
        return parseFloat(parts[0]) * 3600 + parseFloat(parts[1]) * 60 + parseFloat(parts[2]);
    },

    parseAssTime(tc) {
        const parts = tc.split(':');
        return parseFloat(parts[0]) * 3600 + parseFloat(parts[1]) * 60 + parseFloat(parts[2]);
    },

    updateSubtitle() {
        if (!this.subtitleData || this.subtitleData.length === 0) return;
        const video = document.getElementById('video-player');
        if (!video) return;
        const currentTime = video.currentTime;
        const layer = document.getElementById('subtitle-layer');

        // 查找当前字幕
        let found = -1;
        for (let i = 0; i < this.subtitleData.length; i++) {
            if (currentTime >= this.subtitleData[i].start && currentTime <= this.subtitleData[i].end) {
                found = i;
                break;
            }
        }

        if (found !== this.subtitleIndex) {
            this.subtitleIndex = found;
            if (found >= 0) {
                layer.textContent = this.subtitleData[found].text;
                layer.classList.add('active');
            } else {
                layer.textContent = '';
                layer.classList.remove('active');
            }
        }
    },

    // ======== 弹幕 ========

    async toggleDanmaku() {
        this.danmakuEnabled = !this.danmakuEnabled;
        const btn = document.getElementById('danmaku-btn');
        if (btn) {
            btn.classList.toggle('active', this.danmakuEnabled);
            btn.style.color = this.danmakuEnabled ? 'var(--accent)' : '';
        }

        if (this.danmakuEnabled) {
            if (this.danmakuData.length === 0) {
                // 尝试搜索弹幕
                document.getElementById('danmaku-settings-modal').classList.remove('hidden');
            } else {
                this.startDanmakuAnimation();
            }
            this.showVideoInfo('弹幕已开启');
        } else {
            this.stopDanmakuAnimation();
            this.clearDanmakuCanvas();
            this.showVideoInfo('弹幕已关闭');
        }
    },

    async searchDanmaku() {
        const keyword = document.getElementById('danmaku-url-input').value.trim();
        if (!keyword) return;

        const resultsContainer = document.getElementById('danmaku-search-results');
        resultsContainer.innerHTML = '<p class="form-hint">搜索中...</p>';

        // 弹幕增强: 优先使用多来源搜索 (dandanplay + 自定义弹幕API)
        let results = [];
        try {
            results = await this.call('search_danmaku_multi', keyword, this.currentEpIndex + 1);
        } catch (e) {
            // 回退到普通搜索
        }
        if (!results || results.length === 0) {
            results = await this.call('search_danmaku', keyword);
        }

        if (!results || results.length === 0) {
            resultsContainer.innerHTML = '<p class="form-hint">未找到匹配的弹幕</p>';
            return;
        }

        resultsContainer.innerHTML = '';
        results.forEach(anime => {
            const item = document.createElement('div');
            item.className = 'danmaku-result-item';
            const sourceLabel = anime.source ? `<span class="d-source" style="font-size:11px;opacity:0.6;margin-left:6px;">[${this.escape(anime.source)}]</span>` : '';
            item.innerHTML = `
                <div class="d-title">${this.escape(anime.animeTitle)}${sourceLabel}</div>
                <div class="d-sub">${this.escape(anime.type || '')} · 共${anime.episodes || 0}集</div>
            `;
            item.addEventListener('click', async () => {
                const danmaku = await this.call('load_danmaku', anime.animeId, this.currentEpIndex + 1);
                if (danmaku && danmaku.length > 0) {
                    this.danmakuData = danmaku;
                    this.danmakuEnabled = true;
                    this.initDanmakuCanvas();
                    this.startDanmakuAnimation();
                    document.getElementById('danmaku-settings-modal').classList.add('hidden');
                    const btn = document.getElementById('danmaku-btn');
                    if (btn) btn.classList.add('active');
                    this.toast(`已加载 ${danmaku.length} 条弹幕`, 'success');
                } else {
                    this.toast('该集无弹幕', 'error');
                }
            });
            resultsContainer.appendChild(item);
        });
    },

    // 获取配置的弹幕API
    async loadDanmakuApi() {
        try {
            const result = await this.call('get_danmaku_api');
            if (result && result.ok && result.url) {
                const input = document.getElementById('danmaku-url-input');
                if (input && !input.value) {
                    input.value = result.url;
                }
            }
        } catch (e) {
            // 静默失败
        }
    },

    initDanmakuCanvas() {
        if (!this.danmakuEnabled) return;
        const canvas = document.getElementById('danmaku-canvas');
        const video = document.getElementById('video-player');
        if (!canvas || !video) return;

        canvas.width = video.clientWidth;
        canvas.height = video.clientHeight;
        this.danmakuCanvas = canvas;
        this.danmakuCtx = canvas.getContext('2d');

        // 窗口大小变化时重新调整
        window.addEventListener('resize', () => {
            if (this.danmakuCanvas) {
                this.danmakuCanvas.width = video.clientWidth;
                this.danmakuCanvas.height = video.clientHeight;
            }
        });
    },

    startDanmakuAnimation() {
        if (!this.danmakuEnabled || !this.danmakuCanvas) return;
        this.stopDanmakuAnimation();
        this.danmakuTracks = [];
        const trackCount = Math.floor(this.danmakuCanvas.height / (this.danmakuFontSize + 8));
        for (let i = 0; i < trackCount; i++) {
            this.danmakuTracks.push({ items: [], lastTime: 0 });
        }
        this.danmakuAnimId = requestAnimationFrame(() => this.danmakuLoop());
    },

    stopDanmakuAnimation() {
        if (this.danmakuAnimId) {
            cancelAnimationFrame(this.danmakuAnimId);
            this.danmakuAnimId = null;
        }
    },

    danmakuLoop() {
        if (!this.danmakuEnabled || !this.danmakuCanvas) return;
        const video = document.getElementById('video-player');
        if (!video) return;

        const ctx = this.danmakuCtx;
        ctx.clearRect(0, 0, this.danmakuCanvas.width, this.danmakuCanvas.height);

        const currentTime = video.currentTime;
        const speed = this.danmakuSpeed;

        // 发送新弹幕
        for (const dm of this.danmakuData) {
            if (Math.abs(dm.time - currentTime) < 0.3 && !dm._sent) {
                dm._sent = true;
                dm._x = this.danmakuCanvas.width;
                // 找一个空闲轨道
                let trackIdx = -1;
                for (let i = 0; i < this.danmakuTracks.length; i++) {
                    const track = this.danmakuTracks[i];
                    if (track.items.length === 0 || track.items[track.items.length - 1]._x < this.danmakuCanvas.width - 200) {
                        trackIdx = i;
                        break;
                    }
                }
                if (trackIdx >= 0) {
                    dm._track = trackIdx;
                    dm._y = trackIdx * (this.danmakuFontSize + 8) + this.danmakuFontSize;
                    this.danmakuTracks[trackIdx].items.push(dm);
                }
            }
        }

        // 渲染弹幕
        ctx.font = `${this.danmakuFontSize}px sans-serif`;
        ctx.textBaseline = 'middle';
        ctx.globalAlpha = this.danmakuOpacity;

        for (const track of this.danmakuTracks) {
            for (let i = track.items.length - 1; i >= 0; i--) {
                const dm = track.items[i];
                dm._x -= 2 * speed;

                if (dm._x < -ctx.measureText(dm.text).width - 50) {
                    track.items.splice(i, 1);
                    continue;
                }

                // 描边
                ctx.strokeStyle = 'rgba(0,0,0,0.8)';
                ctx.lineWidth = 3;
                ctx.strokeText(dm.text, dm._x, dm._y);
                // 填充
                ctx.fillStyle = dm.color || '#ffffff';
                ctx.fillText(dm.text, dm._x, dm._y);
            }
        }

        ctx.globalAlpha = 1;
        this.danmakuAnimId = requestAnimationFrame(() => this.danmakuLoop());
    },

    updateDanmaku() {
        // 由 timeupdate 触发, 重置已发送标记当 seek 时
        const video = document.getElementById('video-player');
        if (!video || !this.danmakuData.length) return;
        // 如果时间跳跃较大, 重置弹幕
        if (Math.abs(video.currentTime - this.danmakuLastTime) > 5) {
            this.danmakuData.forEach(dm => dm._sent = false);
            this.danmakuTracks.forEach(t => t.items = []);
        }
        this.danmakuLastTime = video.currentTime;
    },

    clearDanmakuCanvas() {
        if (this.danmakuCtx && this.danmakuCanvas) {
            this.danmakuCtx.clearRect(0, 0, this.danmakuCanvas.width, this.danmakuCanvas.height);
        }
        this.danmakuTracks.forEach(t => t.items = []);
    },

    // ======== 外部播放器 ========

    async openExternalPlayer() {
        if (!this.currentPlayUrl) {
            this.toast('没有播放地址', 'error');
            return;
        }
        const result = await this.call('open_external_player', this.currentPlayUrl, this.currentPlayFlag || '');
        if (result.ok) {
            this.toast('已用外部播放器打开', 'success');
        } else {
            this.toast(result.error || '打开失败,请安装 VLC 或 MPV', 'error');
        }
    },

    async copyPlayUrl() {
        if (!this.currentPlayUrl) return;
        try {
            await navigator.clipboard.writeText(this.currentPlayUrl);
            this.toast('播放地址已复制', 'success');
        } catch {
            this.toast('复制失败', 'error');
        }
    },

    updatePlayButton(isPlaying) {
        const btn = document.querySelector('.player-ctrl-btn[onclick="App.togglePlay()"]');
        if (!btn) return;
        if (isPlaying) {
            btn.innerHTML = '<svg viewBox="0 0 24 24" fill="currentColor" width="16" height="16"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>';
            btn.title = '暂停 (空格)';
        } else {
            btn.innerHTML = '<svg viewBox="0 0 24 24" fill="currentColor" width="16" height="16"><polygon points="5 3 19 12 5 21 5 3"/></svg>';
            btn.title = '播放 (空格)';
        }
    },

    updatePlayerTime() {
        const video = document.getElementById('video-player');
        const currentEl = document.getElementById('player-current-time');
        const durationEl = document.getElementById('player-duration');
        if (!video) return;

        if (currentEl) currentEl.textContent = this.formatDuration(video.currentTime);
        if (durationEl) durationEl.textContent = this.formatDuration(video.duration);
    },

    togglePlay() {
        const video = document.getElementById('video-player');
        if (!video) return;
        if (video.paused) {
            video.play().catch(() => {});
        } else {
            video.pause();
        }
    },

    seek(seconds) {
        const video = document.getElementById('video-player');
        if (video) {
            video.currentTime = Math.max(0, Math.min(video.currentTime + seconds, video.duration || 0));
        }
    },

    setSpeed(speed) {
        const video = document.getElementById('video-player');
        if (video) {
            video.playbackRate = speed;
            this.updateSpeedButtons(speed);
        }
    },

    updateSpeedButtons(speed) {
        document.querySelectorAll('.speed-btn').forEach(btn => {
            const btnSpeed = parseFloat(btn.textContent.replace('x', ''));
            btn.classList.toggle('active', btnSpeed === speed);
        });
    },

    toggleMute() {
        const video = document.getElementById('video-player');
        const slider = document.getElementById('volume-slider');
        if (!video) return;

        if (this.isMuted) {
            // 取消静音
            this.isMuted = false;
            video.volume = (this.lastVolume || 100) / 100;
            if (slider) slider.value = this.lastVolume || 100;
        } else {
            // 静音
            this.isMuted = true;
            this.lastVolume = parseInt(slider?.value || 100);
            video.volume = 0;
            if (slider) slider.value = 0;
        }
        this.updateMuteButton();
    },

    updateMuteButton() {
        const btn = document.getElementById('mute-btn');
        if (!btn) return;
        if (this.isMuted) {
            btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><line x1="23" y1="9" x2="17" y2="15"/><line x1="17" y1="9" x2="23" y2="15"/></svg>';
            btn.title = '取消静音 (M)';
        } else {
            btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"/></svg>';
            btn.title = '静音 (M)';
        }
    },

    toggleAutoNext() {
        this.autoNext = !this.autoNext;
        this.call('set_setting', 'autoNext', this.autoNext ? '1' : '0');
        const toggle = document.getElementById('auto-next-toggle');
        if (toggle) toggle.checked = this.autoNext;
        this.updateAutoNextBtn();
        this.toast(this.autoNext ? '自动连播已开启' : '自动连播已关闭');
    },

    updateAutoNextBtn() {
        const btn = document.getElementById('auto-next-btn');
        if (btn) {
            btn.classList.toggle('active', this.autoNext);
            btn.style.color = this.autoNext ? 'var(--accent)' : '';
        }
    },

    async toggleFullscreen() {
        const video = document.getElementById('video-player');
        const wrapper = document.querySelector('.player-wrapper');

        if (!document.fullscreenElement) {
            if (wrapper.requestFullscreen) {
                await wrapper.requestFullscreen();
            } else if (wrapper.webkitRequestFullscreen) {
                await wrapper.webkitRequestFullscreen();
            } else if (video.webkitEnterFullscreen) {
                video.webkitEnterFullscreen();
                return;
            }
            this.isFullscreen = true;
        } else {
            if (document.exitFullscreen) {
                await document.exitFullscreen();
            } else if (document.webkitExitFullscreen) {
                await document.webkitExitFullscreen();
            }
            this.isFullscreen = false;
        }
    },

    async togglePiP() {
        const video = document.getElementById('video-player');
        try {
            if (document.pictureInPictureElement) {
                await document.exitPictureInPicture();
            } else if (video.requestPictureInPicture) {
                await video.requestPictureInPicture();
            } else {
                this.toast('当前浏览器不支持画中画', 'error');
            }
        } catch (e) {
            this.toast('画中画切换失败', 'error');
        }
    },

    prevEpisode() {
        if (this.currentEpIndex > 0) {
            const line = this.currentEpisodes[this.currentLine];
            if (!line) return;
            const ep = line.episodes[this.currentEpIndex - 1];
            this.playVideo(this.currentSiteKey, line.name, ep.url, this.currentEpIndex - 1);
        } else {
            this.toast('已经是第一集');
        }
    },

    nextEpisode() {
        const line = this.currentEpisodes[this.currentLine];
        if (!line) return;
        if (this.currentEpIndex < line.episodes.length - 1) {
            const ep = line.episodes[this.currentEpIndex + 1];
            this.playVideo(this.currentSiteKey, line.name, ep.url, this.currentEpIndex + 1);
        } else {
            // 尝试切换到下一个线路
            if (this.currentLine < this.currentEpisodes.length - 1) {
                this.currentLine++;
                this.currentEpIndex = 0;
                const newLine = this.currentEpisodes[this.currentLine];
                if (newLine.episodes.length > 0) {
                    this.playVideo(this.currentSiteKey, newLine.name, newLine.episodes[0].url, 0);
                }
            } else {
                this.toast('已经是最后一集');
            }
        }
    },

    async downloadCurrent() {
        if (!this.currentDetail) {
            this.toast('没有可下载的内容', 'error');
            return;
        }

        const line = this.currentEpisodes[this.currentLine];
        if (!line) return;
        const ep = line.episodes[this.currentEpIndex];
        if (!ep) return;

        this.showLoading('添加下载任务...');
        const result = await this.call('add_download',
            this.currentDetail.vod_name, ep.name, this.currentPlayUrl || ep.url);
        this.hideLoading();

        if (result.ok) {
            this.toast(`已添加下载: ${ep.name}`, 'success');
        } else {
            this.toast('下载添加失败', 'error');
        }
    },

    async playVideo(siteKey, flag, vid, epIndex) {
        this.currentEpIndex = epIndex;
        this.currentPlayFlag = flag;
        this.currentPlayVid = vid;

        document.querySelectorAll('.episode-item').forEach((el, idx) => {
            el.classList.toggle('playing', idx === epIndex);
        });

        this.showLoading('解析播放地址...');
        const data = await this.call('player_content', siteKey, flag, vid);
        this.hideLoading();

        if (data.error) {
            // 播放失败自动换源
            this.toast('播放地址解析失败, 正在尝试换源...', '');
            const switched = await this.autoSwitchSource(siteKey, this.currentDetail?.vod_id || '', flag, vid);
            if (!switched) {
                this.toast(data.error, 'error');
            }
            return;
        }

        let playUrl = data.url;
        const headers = data.header || {};
        this.currentPlayUrl = playUrl;

        if (headers && Object.keys(headers).length > 0) {
            const ua = headers['User-Agent'] || headers['user-agent'] || '';
            const ref = headers['Referer'] || headers['referer'] || '';
            playUrl = await this.call('build_proxy_url', playUrl, ua, ref);
        }

        this.closeDetail();
        document.getElementById('player-modal').classList.remove('hidden');

        await this.playStream(playUrl);

        const line = this.currentEpisodes[this.currentLine];
        const ep = line ? line.episodes[epIndex] : null;
        document.getElementById('player-info').textContent =
            `${this.currentDetail?.vod_name || ''} - ${ep ? ep.name : ''}`;

        this.renderPlayerEpisodes();

        // 保存播放历史 (无痕模式下不记录)
        if (!this.incognitoMode) {
            try {
                const sites = await this.call('get_sites');
                const siteInfo = sites.find(s => s.key === siteKey);
                await this.call('add_history', this.currentDetail.vod_id, this.currentDetail.vod_name,
                    this.currentDetail.vod_pic, siteKey, siteInfo?.name || '',
                    epIndex, ep?.name || '', vid, 0, 0, this.currentLine);
            } catch (e) {
                // 静默失败
            }
        }

        // 启动进度记录
        this.startHistoryTimer();

        // 恢复播放进度
        if (this.resumePlay) {
            const history = await this.call('get_history_item', this.currentDetail.vod_id, siteKey);
            if (history && history.position > 5) {
                const video = document.getElementById('video-player');
                video.addEventListener('loadedmetadata', () => {
                    video.currentTime = history.position;
                    this.toast(`已恢复至 ${this.formatDuration(history.position)}`);
                }, { once: true });
            }
        }
    },

    // ======== 播放失败自动换源 ========

    async autoSwitchSource(siteKey, vodId, flag, vid) {
        if (this.autoSwitching) return false;
        this.autoSwitching = true;
        try {
            this.toast('正在尝试其他线路或站点...', '');
            const result = await this.call('auto_switch_source', siteKey, vodId, flag, vid);
            if (result && result.ok && result.url) {
                this.toast(`已换源: ${result.source === 'other_site' ? (result.site_name || '其他站点') : '其他线路'}`, 'success');

                // 更新当前播放信息
                this.currentPlayFlag = result.flag;
                this.currentPlayVid = result.vid;
                if (result.site_key && result.site_key !== siteKey) {
                    this.currentSiteKey = result.site_key;
                }

                let playUrl = result.url;
                const headers = result.header || {};
                this.currentPlayUrl = playUrl;

                if (headers && Object.keys(headers).length > 0) {
                    const ua = headers['User-Agent'] || headers['user-agent'] || '';
                    const ref = headers['Referer'] || headers['referer'] || '';
                    playUrl = await this.call('build_proxy_url', playUrl, ua, ref);
                }

                this.closeDetail();
                document.getElementById('player-modal').classList.remove('hidden');
                await this.playStream(playUrl);

                const line = this.currentEpisodes[this.currentLine];
                const ep = line ? line.episodes[this.currentEpIndex] : null;
                document.getElementById('player-info').textContent =
                    `${this.currentDetail?.vod_name || ''} - ${ep ? ep.name : ''}`;
                this.renderPlayerEpisodes();
                this.startHistoryTimer();
                return true;
            }
            return false;
        } catch (e) {
            console.error('自动换源失败:', e);
            return false;
        } finally {
            this.autoSwitching = false;
        }
    },

    startHistoryTimer() {
        if (this.historyTimer) clearInterval(this.historyTimer);
        this.historyTimer = setInterval(async () => {
            const video = document.getElementById('video-player');
            if (!video || !this.currentDetail) return;
            if (video.currentTime > 0) {
                // 无痕模式下不记录进度
                if (this.incognitoMode) return;
                await this.call('update_history_position',
                    this.currentDetail.vod_id, this.currentSiteKey,
                    Math.floor(video.currentTime), Math.floor(video.duration || 0));
            }
        }, 10000);
    },

    renderPlayerEpisodes() {
        const container = document.getElementById('player-episodes');
        container.innerHTML = '';

        const line = this.currentEpisodes[this.currentLine];
        if (!line) return;

        line.episodes.forEach((ep, idx) => {
            const el = document.createElement('div');
            el.className = 'episode-item' + (idx === this.currentEpIndex ? ' playing' : '');
            el.textContent = ep.name;
            el.addEventListener('click', () => {
                this.playVideo(this.currentSiteKey, line.name, ep.url, idx);
            });
            container.appendChild(el);
        });
    },

    async playStream(url) {
        const video = document.getElementById('video-player');

        if (this.hls) {
            this.hls.destroy();
            this.hls = null;
        }

        // 重置弹幕和字幕
        this.stopDanmakuAnimation();
        this.clearDanmakuCanvas();
        this.danmakuData = [];
        if (this.danmakuEnabled) {
            const btn = document.getElementById('danmaku-btn');
            if (btn) { btn.classList.remove('active'); btn.style.color = ''; }
            this.danmakuEnabled = false;
        }
        this.subtitleData = [];
        this.subtitleIndex = -1;
        const subtitleLayer = document.getElementById('subtitle-layer');
        if (subtitleLayer) { subtitleLayer.textContent = ''; subtitleLayer.classList.remove('active'); }

        // 重置进度条
        document.getElementById('progress-played').style.width = '0%';
        document.getElementById('progress-buffered').style.width = '0%';
        document.getElementById('progress-thumb').style.left = '0%';

        // 显示缓冲指示器
        document.getElementById('player-buffering')?.classList.remove('hidden');

        // 设置默认速度
        const savedSpeed = await this.call('get_setting', 'playSpeed', '1');
        video.playbackRate = parseFloat(savedSpeed) || 1;
        this.updateSpeedButtons(parseFloat(savedSpeed) || 1);

        // 恢复音量
        const savedVolume = await this.call('get_setting', 'volume', '100');
        const vol = parseInt(savedVolume);
        video.volume = vol / 100;
        const slider = document.getElementById('volume-slider');
        if (slider) slider.value = vol;

        if (url.includes('.m3u8') || url.includes('m3u8')) {
            if (window.Hls && Hls.isSupported()) {
                this.hls = new Hls({
                    maxBufferLength: 30,
                    maxMaxBufferLength: 60,
                    enableWorker: true,
                    lowLatencyMode: false,
                });
                this.hls.loadSource(url);
                this.hls.attachMedia(video);
                this.hls.on(Hls.Events.MANIFEST_PARSED, () => video.play().catch(() => {}));
                this.hls.on(Hls.Events.ERROR, (e, data) => {
                    if (data.fatal) {
                        switch (data.type) {
                            case Hls.ErrorTypes.NETWORK_ERROR:
                                this.hls.startLoad();
                                break;
                            case Hls.ErrorTypes.MEDIA_ERROR:
                                this.hls.recoverMediaError();
                                break;
                            default:
                                this.toast('播放失败,可能是格式不兼容', 'error');
                                this.hls.destroy();
                                break;
                        }
                    }
                });
            } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
                video.src = url;
                video.play().catch(() => {});
            } else {
                this.toast('不支持 HLS 播放', 'error');
            }
        } else {
            video.src = url;
            video.play().catch(() => {
                this.toast('播放失败', 'error');
                this._handlePlaybackError();
            });
        }

        // 视频播放错误时自动换源
        video.onerror = () => {
            this._handlePlaybackError();
        };
    },

    // 播放失败时尝试自动换源
    _handlePlaybackError() {
        if (this.autoSwitching) return;
        if (this.currentDetail && this.currentSiteKey && this.currentPlayFlag && this.currentPlayVid) {
            this.toast('播放失败, 正在尝试换源...', '');
            this.autoSwitchSource(this.currentSiteKey, this.currentDetail.vod_id, this.currentPlayFlag, this.currentPlayVid);
        }
    },

    closePlayer() {
        // 保存音量
        const video = document.getElementById('video-player');
        if (video) {
            const vol = Math.round(video.volume * 100);
            this.call('set_setting', 'volume', String(vol));
        }

        // 最后保存一次进度 (无痕模式不保存)
        if (video && this.currentDetail && video.currentTime > 0 && !this.incognitoMode) {
            this.call('update_history_position',
                this.currentDetail.vod_id, this.currentSiteKey,
                Math.floor(video.currentTime), Math.floor(video.duration || 0));
        }

        if (this.historyTimer) {
            clearInterval(this.historyTimer);
            this.historyTimer = null;
        }

        // 停止弹幕
        this.stopDanmakuAnimation();
        this.clearDanmakuCanvas();
        this.danmakuData = [];
        this.danmakuEnabled = false;
        const danmakuBtn = document.getElementById('danmaku-btn');
        if (danmakuBtn) {
            danmakuBtn.classList.remove('active');
            danmakuBtn.style.color = '';
        }

        // 清除字幕
        this.subtitleData = [];
        this.subtitleIndex = -1;
        const subtitleLayer = document.getElementById('subtitle-layer');
        if (subtitleLayer) {
            subtitleLayer.textContent = '';
            subtitleLayer.classList.remove('active');
        }

        // 重置画面比例
        this.setAspectRatio('default');

        if (video) {
            video.pause();
            video.removeAttribute('src');
            video.load();
        }

        if (this.hls) {
            this.hls.destroy();
            this.hls = null;
        }

        // 退出全屏
        if (this.isFullscreen) {
            if (document.exitFullscreen) document.exitFullscreen();
            this.isFullscreen = false;
        }

        // 退出画中画
        if (document.pictureInPictureElement) {
            document.exitPictureInPicture().catch(() => {});
        }

        // 隐藏控制条
        this.showPlayerControls();

        document.getElementById('player-modal').classList.add('hidden');
    },

    // ======== 键盘快捷键 ========

    bindKeyboard() {
        document.addEventListener('keydown', (e) => {
            // 只在播放器打开时响应
            const playerModal = document.getElementById('player-modal');
            if (playerModal.classList.contains('hidden')) {
                // Esc 关闭详情弹窗
                if (e.key === 'Escape') {
                    const detailModal = document.getElementById('detail-modal');
                    if (!detailModal.classList.contains('hidden')) {
                        this.closeDetail();
                    }
                }
                return;
            }

            // 如果焦点在输入框,不处理快捷键
            if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') {
                return;
            }

            const video = document.getElementById('video-player');
            if (!video) return;

            switch (e.key) {
                case ' ':
                    e.preventDefault();
                    this.togglePlay();
                    break;
                case 'ArrowLeft':
                    e.preventDefault();
                    this.seek(-10);
                    break;
                case 'ArrowRight':
                    e.preventDefault();
                    this.seek(10);
                    break;
                case 'ArrowUp':
                    e.preventDefault();
                    {
                        const slider = document.getElementById('volume-slider');
                        const newVol = Math.min(100, parseInt(slider.value) + 10);
                        slider.value = newVol;
                        video.volume = newVol / 100;
                        this.isMuted = false;
                        this.updateMuteButton();
                    }
                    break;
                case 'ArrowDown':
                    e.preventDefault();
                    {
                        const slider = document.getElementById('volume-slider');
                        const newVol = Math.max(0, parseInt(slider.value) - 10);
                        slider.value = newVol;
                        video.volume = newVol / 100;
                        if (newVol === 0) this.isMuted = true;
                        this.updateMuteButton();
                    }
                    break;
                case 'f':
                case 'F':
                    e.preventDefault();
                    this.toggleFullscreen();
                    break;
                case 'n':
                case 'N':
                    e.preventDefault();
                    this.nextEpisode();
                    break;
                case 'p':
                case 'P':
                    e.preventDefault();
                    this.prevEpisode();
                    break;
                case 'm':
                case 'M':
                    e.preventDefault();
                    this.toggleMute();
                    break;
                case 'd':
                case 'D':
                    e.preventDefault();
                    this.toggleDanmaku();
                    break;
                case 'r':
                case 'R':
                    e.preventDefault();
                    this.toggleAspectRatio();
                    break;
                case 's':
                case 'S':
                    e.preventDefault();
                    this.screenshot();
                    break;
                case 'Escape':
                    e.preventDefault();
                    if (this.isFullscreen) {
                        this.toggleFullscreen();
                    } else {
                        this.closePlayer();
                    }
                    break;
            }
        });
    },

    // ======== 直播 ========

    bindLive() {
        document.getElementById('live-reload-btn')?.addEventListener('click', async () => {
            const url = document.getElementById('live-url').value.trim();
            if (url) {
                const type = parseInt(document.querySelector('input[name="live-type"]:checked').value);
                await this.loadLive(url, type);
            }
        });

        // 直播搜索
        const liveSearchInput = document.getElementById('live-search-input');
        if (liveSearchInput) {
            let searchTimer = null;
            liveSearchInput.addEventListener('input', () => {
                if (searchTimer) clearTimeout(searchTimer);
                searchTimer = setTimeout(() => {
                    this.searchLiveChannels(liveSearchInput.value.trim());
                }, 300);
            });
        }

        // 直播标签切换
        document.querySelectorAll('.live-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                document.querySelectorAll('.live-tab').forEach(t => t.classList.remove('active'));
                tab.classList.add('active');
                this.liveTab = tab.dataset.tab;
                this.renderLiveGroups();
            });
        });

        // 上一个/下一个频道
        document.getElementById('live-prev-btn')?.addEventListener('click', () => this.switchLiveChannel(-1));
        document.getElementById('live-next-btn')?.addEventListener('click', () => this.switchLiveChannel(1));
        document.getElementById('live-fav-btn')?.addEventListener('click', () => this.toggleLiveFavorite());
    },

    async loadLive(url, type) {
        const epgUrl = document.getElementById('epg-url').value.trim() ||
            await this.call('get_setting', 'epgUrl', '');

        this.showLoading('加载直播源...');
        const result = await this.call('parse_live', url, type, epgUrl);
        this.hideLoading();

        if (!result.ok) {
            this.toast(result.error || '加载失败', 'error');
            return;
        }

        await this.call('set_setting', 'liveUrl', url);
        const name = url.split('/').pop().split('?')[0].substring(0, 30);
        await this.call('add_live_config', name, url, type);

        this.liveData = result.data;
        this.renderLiveGroups();
        document.getElementById('live-status').textContent =
            `已加载 ${result.data.channel_count} 个频道, ${result.data.group_names.length} 个分组`;
        this.toast(`已加载 ${result.data.channel_count} 个频道`, 'success');

        await this.renderSavedLives();
    },

    renderLiveGroups() {
        if (!this.liveData) return;
        const container = document.getElementById('live-groups');
        container.innerHTML = '';

        let groupsToShow = {};
        let groupNames = [];
        let groupMeta = {}; // groupName -> {needs_password, password}

        if (this.liveTab === 'all') {
            groupsToShow = this.liveData.groups;
            groupNames = this.liveData.group_names || [];
            // 从结构化 group_list 获取密码信息
            if (this.liveData.group_list) {
                this.liveData.group_list.forEach(g => {
                    groupMeta[g.name] = {
                        needs_password: g.needs_password,
                        password: g.password || '',
                    };
                });
            }
        } else if (this.liveTab === 'favorites') {
            this.call('get_live_favorites').then(favs => {
                if (!favs || favs.length === 0) {
                    container.innerHTML = '<div class="empty-state" style="padding:20px"><p>暂无直播收藏</p></div>';
                    return;
                }
                const favGroup = {};
                favs.forEach(f => {
                    const g = f.group_name || '收藏';
                    if (!favGroup[g]) favGroup[g] = [];
                    favGroup[g].push({ name: f.channel_name, url: f.channel_url, logo: f.logo || '' });
                });
                this._renderLiveGroupList(favGroup, Object.keys(favGroup), {});
            });
            return;
        } else if (this.liveTab === 'history') {
            this.call('get_live_history').then(history => {
                if (!history || history.length === 0) {
                    container.innerHTML = '<div class="empty-state" style="padding:20px"><p>暂无直播历史</p></div>';
                    return;
                }
                const histGroup = { '最近观看': [] };
                history.forEach(h => {
                    histGroup['最近观看'].push({ name: h.channel_name, url: h.channel_url, logo: '' });
                });
                this._renderLiveGroupList(histGroup, Object.keys(histGroup), {});
            });
            return;
        }

        this._renderLiveGroupList(groupsToShow, groupNames, groupMeta);
    },

    _renderLiveGroupList(groups, groupNames, groupMeta) {
        const container = document.getElementById('live-groups');
        container.innerHTML = '';
        groupMeta = groupMeta || {};

        groupNames.forEach((name, idx) => {
            const channels = groups[name] || [];
            const meta = groupMeta[name] || {};
            const needsPassword = meta.needs_password && !this.unlockedGroups.has(name);
            const el = document.createElement('div');
            el.className = 'live-group' + (idx === 0 && !needsPassword ? ' active' : '');
            // 密码保护分组显示锁图标
            const lockIcon = needsPassword
                ? ' <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="12" height="12" style="vertical-align:middle;"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>'
                : '';
            el.innerHTML = `${this.escape(name)}${lockIcon} <span style="opacity:0.6">(${channels.length})</span>`;
            el.addEventListener('click', async () => {
                // 密码保护验证
                if (needsPassword) {
                    const password = prompt(`请输入「${name}」分组的密码:`);
                    if (password === null) return; // 用户取消
                    if (password !== meta.password) {
                        this.toast('密码错误', 'error');
                        return;
                    }
                    this.unlockedGroups.add(name);
                }
                document.querySelectorAll('.live-group').forEach(g => g.classList.remove('active'));
                el.classList.add('active');
                this.currentLiveGroup = name;
                this.liveChannelList = channels;
                this.renderLiveChannels(name, channels);
            });
            container.appendChild(el);
        });

        if (groupNames.length > 0) {
            // 自动选中第一个不需要密码的分组
            let firstIdx = 0;
            for (let i = 0; i < groupNames.length; i++) {
                const meta = groupMeta[groupNames[i]] || {};
                if (!meta.needs_password || this.unlockedGroups.has(groupNames[i])) {
                    firstIdx = i;
                    break;
                }
            }
            const firstName = groupNames[firstIdx];
            const meta = groupMeta[firstName] || {};
            if (!meta.needs_password || this.unlockedGroups.has(firstName)) {
                this.currentLiveGroup = firstName;
                this.liveChannelList = groups[firstName] || [];
                this.renderLiveChannels(firstName, this.liveChannelList);
            }
        }
    },

    renderLiveChannels(groupName, channels) {
        const container = document.getElementById('live-channels');
        container.innerHTML = '';

        const chList = channels || (this.liveData.groups[groupName] || []);
        // 从结构化数据中查找完整频道信息 (含 multi_urls, catchup 等)
        const fullChannels = this._getFullChannels(groupName, chList);

        chList.forEach((ch, idx) => {
            const el = document.createElement('div');
            el.className = 'live-channel';
            const fullCh = fullChannels[idx] || ch;
            // 多线路备援显示标记
            const multiBadge = (fullCh.multi_urls && fullCh.multi_urls.length > 0)
                ? ` <span style="opacity:0.5;font-size:11px;">[${fullCh.multi_urls_count || (fullCh.multi_urls.length + 1)}]</span>`
                : '';
            // catchup 支持标记
            const catchupBadge = (fullCh.catchup_source || fullCh.catchup_type)
                ? ' <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="11" height="11" style="vertical-align:middle;" title="支持时移"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>'
                : '';
            if (ch.logo) {
                el.innerHTML = `<img src="${ch.logo}" class="live-channel-logo" onerror="this.style.display='none'">${this.escape(ch.name)}${multiBadge}${catchupBadge}`;
            } else {
                el.innerHTML = `${this.escape(ch.name)}${multiBadge}${catchupBadge}`;
            }
            el.addEventListener('click', () => {
                document.querySelectorAll('.live-channel').forEach(c => c.classList.remove('active'));
                el.classList.add('active');
                this.liveChannelIndex = idx;
                this.playLiveChannel(fullCh, idx);
            });
            container.appendChild(el);
        });
    },

    // 从结构化数据中获取完整频道信息
    _getFullChannels(groupName, chList) {
        if (!this.liveData || !this.liveData.channels) return chList;
        // 按 name 匹配完整频道
        const nameMap = {};
        this.liveData.channels.forEach(c => {
            if (!nameMap[c.name]) nameMap[c.name] = c;
        });
        return chList.map(ch => {
            const full = nameMap[ch.name];
            if (full) {
                return Object.assign({}, ch, full);
            }
            return ch;
        });
    },

    // 多线路备援直播播放
    async playLiveChannel(channel, idx) {
        const name = channel.name;
        // 获取所有备用 URL (主URL + multi_urls, 或 # 分隔)
        let urls = [];
        if (channel.url) {
            // 支持 # 分隔多个 URL
            urls = channel.url.split('#').filter(u => u.trim());
        }
        if (channel.multi_urls && channel.multi_urls.length > 0) {
            channel.multi_urls.forEach(u => {
                if (u && u.trim() && !urls.includes(u)) urls.push(u.trim());
            });
        }
        if (urls.length === 0) {
            this.toast('无有效播放地址', 'error');
            return;
        }

        this.currentLiveChannel = { url: channel.url, name, channel };
        this._liveUrls = urls;
        this._liveUrlIndex = 0;

        // 显示 catchup 按钮 (如果支持)
        this._updateCatchupButton(channel);

        // 逐个尝试播放
        await this._playLiveUrl(urls[0], name);
    },

    async _playLiveUrl(url, name) {
        const area = document.getElementById('live-player-area');
        area.innerHTML = '<video id="live-video" controls autoplay></video>';
        const video = document.getElementById('live-video');
        const controls = document.getElementById('live-controls');

        if (controls) controls.classList.remove('hidden');
        const nameLabel = document.getElementById('live-channel-name');
        if (nameLabel) nameLabel.textContent = name;

        // 检查收藏状态
        const favStatus = await this.call('is_live_favorite', name, url);
        const favBtn = document.getElementById('live-fav-btn');
        if (favBtn) {
            favBtn.classList.toggle('favorited', favStatus.is_favorite);
            favBtn.textContent = favStatus.is_favorite ? '已收藏' : '收藏';
        }

        if (this.liveHls) {
            this.liveHls.destroy();
            this.liveHls = null;
        }

        let liveErrorCount = 0;
        if (url.includes('.m3u8') || url.includes('m3u8')) {
            if (window.Hls && Hls.isSupported()) {
                this.liveHls = new Hls({
                    maxBufferLength: 10,
                    liveSyncDuration: 3,
                    enableWorker: true,
                });
                this.liveHls.loadSource(url);
                this.liveHls.attachMedia(video);
                this.liveHls.on(Hls.Events.MANIFEST_PARSED, () => video.play().catch(() => {}));
                this.liveHls.on(Hls.Events.ERROR, (e, data) => {
                    if (data.fatal) {
                        switch (data.type) {
                            case Hls.ErrorTypes.NETWORK_ERROR:
                                liveErrorCount++;
                                if (liveErrorCount > 2) {
                                    this._tryNextLiveUrl(name);
                                } else {
                                    this.liveHls.startLoad();
                                }
                                break;
                            case Hls.ErrorTypes.MEDIA_ERROR:
                                this.liveHls.recoverMediaError();
                                break;
                            default:
                                this._tryNextLiveUrl(name);
                                break;
                        }
                    }
                });
            } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
                video.src = url;
                video.play().catch(() => this._tryNextLiveUrl(name));
            }
        } else {
            const proxyUrl = await this.call('build_proxy_url', url, '', '');
            video.src = proxyUrl;
            video.play().catch(() => {
                this._tryNextLiveUrl(name);
            });
        }

        // 保存直播历史
        await this.call('add_live_history', name, url, this.currentLiveGroup || '');

        // 加载 EPG
        await this.loadEpgForChannel(name);
    },

    // 多线路备援: 尝试下一个 URL
    async _tryNextLiveUrl(name) {
        if (!this._liveUrls || this._liveUrlIndex === undefined) {
            this.toast('直播流加载失败', 'error');
            return;
        }
        this._liveUrlIndex++;
        if (this._liveUrlIndex < this._liveUrls.length) {
            const nextUrl = this._liveUrls[this._liveUrlIndex];
            this.toast(`正在尝试备用线路 ${this._liveUrlIndex + 1}/${this._liveUrls.length}...`, '');
            await this._playLiveUrl(nextUrl, name);
        } else {
            this.toast('所有线路均播放失败', 'error');
        }
    },

    // ======== Catchup 时移 ========

    _updateCatchupButton(channel) {
        let btn = document.getElementById('live-catchup-btn');
        const supportsCatchup = channel && (channel.catchup_source || channel.catchup_type);
        if (!btn) {
            if (!supportsCatchup) return;
            btn = document.createElement('button');
            btn.id = 'live-catchup-btn';
            btn.className = 'btn-sm';
            btn.title = '时移回看';
            btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg> 时移';
            btn.addEventListener('click', () => this.showCatchupDialog());
            const controls = document.getElementById('live-controls');
            if (controls) controls.appendChild(btn);
        }
        btn.style.display = supportsCatchup ? '' : 'none';
        this._catchupChannel = supportsCatchup ? channel : null;
    },

    showCatchupDialog() {
        if (!this._catchupChannel) {
            this.toast('该频道不支持时移', 'error');
            return;
        }
        const name = this._catchupChannel.name;
        const url = this._catchupChannel.url;
        const now = new Date();
        const startDefault = new Date(now.getTime() - 3600000); // 默认1小时前
        const startStr = startDefault.toISOString().slice(0, 19);
        const endStr = now.toISOString().slice(0, 19);

        const startTime = prompt('请输入开始时间 (YYYY-MM-DDTHH:MM:SS):', startStr);
        if (!startTime) return;
        const endTime = prompt('请输入结束时间 (YYYY-MM-DDTHH:MM:SS):', endStr);
        if (!endTime) return;

        this.playCatchup(name, url, startTime, endTime);
    },

    async playCatchup(channelName, channelUrl, startTime, endTime) {
        try {
            this.showLoading('构建时移地址...');
            const result = await this.call('build_catchup_url', channelName, channelUrl, startTime, endTime);
            this.hideLoading();

            if (result && result.ok && result.url) {
                this.toast('时移地址已生成, 正在播放...', 'success');
                // 使用普通播放器播放时移地址
                const area = document.getElementById('live-player-area');
                area.innerHTML = '<video id="live-video" controls autoplay></video>';
                const video = document.getElementById('live-video');

                if (result.url.includes('.m3u8') || result.url.includes('m3u8')) {
                    if (window.Hls && Hls.isSupported()) {
                        if (this.liveHls) this.liveHls.destroy();
                        this.liveHls = new Hls({ maxBufferLength: 30, enableWorker: true });
                        this.liveHls.loadSource(result.url);
                        this.liveHls.attachMedia(video);
                        this.liveHls.on(Hls.Events.MANIFEST_PARSED, () => video.play().catch(() => {}));
                    } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
                        video.src = result.url;
                        video.play().catch(() => {});
                    }
                } else {
                    video.src = result.url;
                    video.play().catch(() => this.toast('时移播放失败', 'error'));
                }
            } else {
                this.toast(result?.error || '时移地址构建失败', 'error');
            }
        } catch (e) {
            this.hideLoading();
            this.toast('时移播放失败', 'error');
        }
    },

    async searchLiveChannels(keyword) {
        if (!keyword) {
            if (this.liveData) this.renderLiveGroups();
            return;
        }

        const results = await this.call('search_live_channels', keyword);
        const container = document.getElementById('live-groups');
        container.innerHTML = '';

        if (!results || results.length === 0) {
            container.innerHTML = '<div class="empty-state" style="padding:20px"><p>未找到匹配频道</p></div>';
            document.getElementById('live-channels').innerHTML = '';
            return;
        }

        // 按分组组织搜索结果
        const groups = {};
        results.forEach(ch => {
            const g = ch.group || '搜索结果';
            if (!groups[g]) groups[g] = [];
            groups[g].push({ name: ch.name, url: ch.url, logo: ch.logo || '' });
        });

        this._renderLiveGroupList(groups, Object.keys(groups));
    },

    // playLive: 兼容入口, 转发到 playLiveChannel
    async playLive(url, name) {
        await this.playLiveChannel({ url, name }, this.liveChannelIndex);
    },

    switchLiveChannel(direction) {
        if (this.liveChannelIndex < 0 || !this.liveChannelList) return;
        const newIndex = this.liveChannelIndex + direction;
        if (newIndex < 0 || newIndex >= this.liveChannelList.length) {
            this.toast(direction > 0 ? '已经是最后一个频道' : '已经是第一个频道');
            return;
        }
        this.liveChannelIndex = newIndex;
        const ch = this.liveChannelList[newIndex];

        // 更新选中状态
        document.querySelectorAll('.live-channel').forEach((c, idx) => {
            c.classList.toggle('active', idx === newIndex);
        });

        // 获取完整频道信息
        const fullChannels = this._getFullChannels(this.currentLiveGroup, this.liveChannelList);
        const fullCh = fullChannels[newIndex] || ch;
        this.playLiveChannel(fullCh, newIndex);
    },

    async toggleLiveFavorite() {
        if (!this.currentLiveChannel) return;
        const { url, name } = this.currentLiveChannel;
        const favStatus = await this.call('is_live_favorite', name, url);

        if (favStatus.is_favorite) {
            await this.call('remove_live_favorite', name, url);
            this.toast('已取消收藏');
        } else {
            await this.call('add_live_favorite', name, url, this.currentLiveGroup || '');
            this.toast('已收藏', 'success');
        }

        // 更新按钮状态
        const newStatus = await this.call('is_live_favorite', name, url);
        const favBtn = document.getElementById('live-fav-btn');
        if (favBtn) {
            favBtn.classList.toggle('favorited', newStatus.is_favorite);
            favBtn.textContent = newStatus.is_favorite ? '已收藏' : '收藏';
        }
    },

    async loadEpgForChannel(channelName) {
        const epgEl = document.getElementById('live-epg');
        const current = await this.call('get_current_epg', channelName);
        const progs = await this.call('get_epg', channelName);

        if (!current && (!progs || progs.length === 0)) {
            epgEl.classList.remove('active');
            epgEl.innerHTML = '';
            return;
        }

        epgEl.classList.add('active');
        let html = '';
        if (current) {
            html += `<div class="epg-now">正在播放: ${this.escape(current.title)}</div>`;
        }
        if (progs && progs.length > 0) {
            html += '<div class="epg-list">';
            progs.slice(0, 8).forEach(p => {
                const isCurrent = current && p.title === current.title;
                html += `<div class="epg-item${isCurrent ? ' current' : ''}"><span class="epg-time">${this.formatEpgTime(p.start)}</span>${this.escape(p.title)}</div>`;
            });
            html += '</div>';
        }
        epgEl.innerHTML = html;
    },

    // ======== 收藏 ========

    bindFavorites() {
        document.getElementById('refresh-fav-btn')?.addEventListener('click', () => this.loadFavorites());

        // 收藏标签切换
        document.querySelectorAll('#view-favorites .fav-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                document.querySelectorAll('#view-favorites .fav-tab').forEach(t => t.classList.remove('active'));
                tab.classList.add('active');
                this.favTab = tab.dataset.tab;
                this.loadFavorites();
            });
        });
    },

    async loadFavorites() {
        if (this.favTab === 'vod') {
            await this.loadVodFavorites();
        } else {
            await this.loadLiveFavorites();
        }
    },

    async loadVodFavorites() {
        const favs = await this.call('get_favorites');
        const grid = document.getElementById('favorites-grid');
        grid.innerHTML = '';

        if (!favs || favs.length === 0) {
            grid.innerHTML = '<div class="empty-state"><p>暂无收藏</p><p class="hint">在视频详情中点击收藏按钮</p></div>';
            return;
        }

        favs.forEach(fav => {
            const card = document.createElement('div');
            card.className = 'vod-card';
            card.innerHTML = `
                <div class="poster">
                    <img src="${fav.vod_pic || this.placeholderImg()}" onerror="this.src='${this.placeholderImg()}'" loading="lazy">
                    ${fav.vod_remarks ? `<span class="remarks">${this.escape(fav.vod_remarks)}</span>` : ''}
                </div>
                <div class="vod-name">${this.escape(fav.vod_name)}</div>
                <div class="vod-sub">${this.escape(fav.site_name)}</div>
            `;
            card.addEventListener('click', () => {
                this.showDetail(fav.site_key, fav.vod_id);
            });
            grid.appendChild(card);
        });
    },

    async loadLiveFavorites() {
        const favs = await this.call('get_live_favorites');
        const grid = document.getElementById('favorites-grid');
        grid.innerHTML = '';

        if (!favs || favs.length === 0) {
            grid.innerHTML = '<div class="empty-state"><p>暂无直播收藏</p><p class="hint">在直播频道中点击收藏按钮</p></div>';
            return;
        }

        const list = document.createElement('div');
        list.className = 'live-fav-list';
        favs.forEach(fav => {
            const item = document.createElement('div');
            item.className = 'live-fav-item';
            item.innerHTML = `
                ${fav.logo ? `<img src="${fav.logo}" class="live-channel-logo" onerror="this.style.display='none'">` : ''}
                <div class="live-fav-info">
                    <div class="live-fav-name">${this.escape(fav.channel_name)}</div>
                    <div class="live-fav-group">${this.escape(fav.group_name || '')}</div>
                </div>
                <button class="btn-sm btn-danger" data-name="${this.escape(fav.channel_name)}" data-url="${this.escape(fav.channel_url)}">删除</button>
            `;
            item.addEventListener('click', (e) => {
                if (e.target.tagName === 'BUTTON') {
                    e.stopPropagation();
                    this.call('remove_live_favorite', fav.channel_name, fav.channel_url);
                    this.loadLiveFavorites();
                    return;
                }
                // 切换到直播页面播放
                this.switchView('live');
                this.playLive(fav.channel_url, fav.channel_name);
            });
            list.appendChild(item);
        });
        grid.appendChild(list);
    },

    // ======== 历史 ========

    bindHistory() {
        document.getElementById('clear-history-btn')?.addEventListener('click', async () => {
            if (confirm('确定清空所有播放历史?')) {
                await this.call('delete_history', '', '', true);
                this.loadHistory();
                this.toast('已清空播放历史');
            }
        });

        // 历史标签切换
        document.querySelectorAll('#view-history .fav-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                document.querySelectorAll('#view-history .fav-tab').forEach(t => t.classList.remove('active'));
                tab.classList.add('active');
                this.historyTab = tab.dataset.tab;
                this.loadHistory();
            });
        });
    },

    async loadHistory() {
        if (this.historyTab === 'vod') {
            await this.loadVodHistory();
        } else {
            await this.loadLiveHistory();
        }
    },

    async loadVodHistory() {
        const history = await this.call('get_history', 60);
        const list = document.getElementById('history-list');
        list.innerHTML = '';

        if (!history || history.length === 0) {
            list.innerHTML = '<div class="empty-state"><p>暂无播放记录</p></div>';
            return;
        }

        history.forEach(h => {
            const item = document.createElement('div');
            item.className = 'history-item';

            const progress = h.duration > 0 ? (h.position / h.duration * 100) : 0;
            const progressHtml = h.duration > 0
                ? `<div class="h-progress"><div class="h-progress-bar" style="width:${progress}%"></div></div>`
                : '';

            item.innerHTML = `
                <img class="h-poster" src="${h.vod_pic || this.placeholderImg()}" onerror="this.src='${this.placeholderImg()}'">
                <div class="h-info">
                    <div class="h-name">${this.escape(h.vod_name)}</div>
                    <div class="h-sub">${this.escape(h.site_name)} - ${this.escape(h.episode_name || '')}</div>
                </div>
                ${progressHtml}
                <span class="h-time">${this.formatTime(h.updated_at)}</span>
                <span class="h-delete" data-id="${h.id}">删除</span>
            `;

            item.addEventListener('click', (e) => {
                if (e.target.classList.contains('h-delete')) {
                    e.stopPropagation();
                    this.call('delete_history', h.vod_id, h.site_key);
                    this.loadHistory();
                    return;
                }
                this.showDetail(h.site_key, h.vod_id);
            });

            list.appendChild(item);
        });
    },

    async loadLiveHistory() {
        const history = await this.call('get_live_history', 60);
        const list = document.getElementById('history-list');
        list.innerHTML = '';

        if (!history || history.length === 0) {
            list.innerHTML = '<div class="empty-state"><p>暂无直播记录</p></div>';
            return;
        }

        history.forEach(h => {
            const item = document.createElement('div');
            item.className = 'history-item';
            item.innerHTML = `
                <div class="h-info">
                    <div class="h-name">${this.escape(h.channel_name)}</div>
                    <div class="h-sub">${this.escape(h.group_name || '')}</div>
                </div>
                <span class="h-time">${this.formatTime(h.updated_at)}</span>
                <span class="h-delete" data-name="${this.escape(h.channel_name)}" data-url="${this.escape(h.channel_url)}">删除</span>
            `;

            item.addEventListener('click', (e) => {
                if (e.target.classList.contains('h-delete')) {
                    e.stopPropagation();
                    // 直播历史没有单独删除接口,使用刷新即可
                    this.loadLiveHistory();
                    return;
                }
                this.switchView('live');
                this.playLive(h.channel_url, h.channel_name);
            });

            list.appendChild(item);
        });
    },

    // ======== 下载管理 (增强版) ========

    bindDownloads() {
        document.getElementById('refresh-downloads-btn')?.addEventListener('click', () => this.loadDownloads());
        document.getElementById('clear-completed-btn')?.addEventListener('click', async () => {
            await this.call('clear_completed_downloads');
            this.toast('已清除完成的下载', 'success');
            this.loadDownloads();
        });
        document.getElementById('open-dl-folder-btn')?.addEventListener('click', async () => {
            await this.call('open_download_folder');
        });

        // 批量下载弹窗
        document.getElementById('select-all-eps-btn')?.addEventListener('click', () => {
            document.querySelectorAll('#batch-episode-list input[type="checkbox"]').forEach(cb => cb.checked = true);
        });
        document.getElementById('deselect-all-eps-btn')?.addEventListener('click', () => {
            document.querySelectorAll('#batch-episode-list input[type="checkbox"]').forEach(cb => cb.checked = false);
        });
        document.getElementById('start-batch-download-btn')?.addEventListener('click', () => this.startBatchDownload());

        // 下载设置
        document.getElementById('save-speed-limit-btn')?.addEventListener('click', async () => {
            const limit = parseInt(document.getElementById('download-speed-limit').value) || 0;
            await this.call('set_download_speed_limit', limit);
            this.toast(limit > 0 ? `已设置限速 ${limit} KB/s` : '已取消限速', 'success');
        });

        // 系统设置
        document.getElementById('autostart-toggle')?.addEventListener('change', async (e) => {
            if (e.target.checked) {
                const result = await this.call('enable_autostart');
                this.toast(result.ok ? '已设置开机自启' : '设置失败', result.ok ? 'success' : 'error');
            } else {
                await this.call('disable_autostart');
                this.toast('已取消开机自启');
            }
        });

        document.getElementById('minimize-tray-toggle')?.addEventListener('change', async (e) => {
            await this.call('set_setting', 'minimizeToTray', e.target.checked ? '1' : '0');
            this.toast(e.target.checked ? '已开启最小化到托盘' : '已关闭');
        });

        document.getElementById('always-on-top-toggle')?.addEventListener('change', async (e) => {
            await this.call('set_window_always_on_top', e.target.checked);
            this.toast(e.target.checked ? '窗口已置顶' : '已取消置顶');
        });
    },

    async loadDownloads() {
        const downloads = await this.call('get_downloads');
        const list = document.getElementById('downloads-list');
        const statsEl = document.getElementById('download-stats');
        list.innerHTML = '';

        if (!downloads || downloads.length === 0) {
            list.innerHTML = '<div class="empty-state"><p>暂无下载任务</p><p class="hint">在播放器中点击下载按钮</p></div>';
            if (statsEl) statsEl.innerHTML = '';
            return;
        }

        // 统计
        let downloading = 0, completed = 0, failed = 0, paused = 0;
        let totalSpeed = 0;
        downloads.forEach(dl => {
            const status = dl.live_status || dl.status;
            if (status === 'downloading') { downloading++; totalSpeed += dl.speed || 0; }
            else if (status === 'completed') completed++;
            else if (status === 'failed') failed++;
            else if (status === 'paused') paused++;
        });

        if (statsEl) {
            statsEl.innerHTML = `
                <div class="download-stat-item">下载中: <span class="stat-value">${downloading}</span></div>
                <div class="download-stat-item">已暂停: <span class="stat-value">${paused}</span></div>
                <div class="download-stat-item">已完成: <span class="stat-value">${completed}</span></div>
                <div class="download-stat-item">失败: <span class="stat-value">${failed}</span></div>
                ${downloading > 0 ? `<div class="download-stat-item">总速度: <span class="stat-value">${this.formatFileSize(totalSpeed)}/s</span></div>` : ''}
            `;
        }

        downloads.forEach(dl => {
            const item = document.createElement('div');
            item.className = 'download-item';

            const status = dl.live_status || dl.status;
            const progress = dl.progress || (dl.file_size > 0 ? (dl.downloaded / dl.file_size * 100) : 0);
            const speed = dl.speed || 0;
            const eta = dl.eta || 0;

            const statusText = {
                'pending': '等待中',
                'downloading': `下载中 ${progress.toFixed(1)}%`,
                'paused': '已暂停',
                'completed': '已完成',
                'failed': '失败',
                'cancelled': '已取消',
            }[status] || status;

            const statusClass = 'dl-status ' + (status || '');

            let actionsHtml = '';
            if (status === 'downloading') {
                actionsHtml += `<button class="dl-action-btn" data-action="pause" data-id="${dl.id}" title="暂停"><svg viewBox="0 0 24 24" fill="currentColor" width="14" height="14"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg></button>`;
            } else if (status === 'paused') {
                actionsHtml += `<button class="dl-action-btn" data-action="resume" data-id="${dl.id}" title="恢复"><svg viewBox="0 0 24 24" fill="currentColor" width="14" height="14"><polygon points="5 3 19 12 5 21 5 3"/></svg></button>`;
            } else if (status === 'failed' || status === 'cancelled') {
                actionsHtml += `<button class="dl-action-btn" data-action="retry" data-id="${dl.id}" title="重试"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg></button>`;
            }
            if (status === 'completed') {
                actionsHtml += `<button class="dl-action-btn" data-action="open" data-path="${this.escape(dl.file_path)}" title="打开"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg></button>`;
            }
            if (status !== 'completed') {
                actionsHtml += `<button class="dl-action-btn danger" data-action="cancel" data-id="${dl.id}" title="取消/删除"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>`;
            }
            actionsHtml += `<button class="dl-action-btn danger" data-action="del" data-id="${dl.id}" title="删除记录"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg></button>`;

            const progressHtml = (status === 'downloading' || status === 'paused') ? `
                <div class="dl-progress-section">
                    <div class="dl-progress-bar-wrap">
                        <div class="dl-progress"><div class="dl-progress-bar" style="width:${progress}%"></div></div>
                        <span class="dl-progress-text">${progress.toFixed(1)}%</span>
                    </div>
                    <div style="display:flex;gap:12px;margin-top:4px;">
                        ${dl.file_size > 0 ? `<span class="dl-eta">${this.formatFileSize(dl.downloaded)} / ${this.formatFileSize(dl.file_size)}</span>` : ''}
                        ${speed > 0 ? `<span class="dl-speed">${this.formatFileSize(speed)}/s</span>` : ''}
                        ${eta > 0 ? `<span class="dl-eta">剩余 ${this.formatDuration(eta)}</span>` : ''}
                    </div>
                </div>
            ` : '';

            item.innerHTML = `
                <div class="dl-main">
                    <div class="dl-icon">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="20" height="20">
                            ${status === 'completed' ? '<polyline points="20 6 9 17 4 12"/>' : '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>'}
                        </svg>
                    </div>
                    <div class="dl-info">
                        <div class="dl-name">${this.escape(dl.vod_name)}${dl.episode_name ? ' - ' + this.escape(dl.episode_name) : ''}</div>
                        <div class="dl-sub">
                            <span class="${statusClass}">${statusText}</span>
                            <span class="dl-time">${this.formatTime(dl.created_at)}</span>
                            ${dl.error ? `<span style="color:var(--danger);">${this.escape(dl.error)}</span>` : ''}
                        </div>
                    </div>
                    <div class="dl-actions">${actionsHtml}</div>
                </div>
                ${progressHtml}
            `;

            // 绑定按钮事件
            item.querySelectorAll('[data-action]').forEach(btn => {
                btn.addEventListener('click', async (e) => {
                    e.stopPropagation();
                    const action = btn.dataset.action;
                    const id = parseInt(btn.dataset.id);
                    if (action === 'pause') await this.call('pause_download', id);
                    else if (action === 'resume') await this.call('resume_download', id);
                    else if (action === 'retry') await this.call('retry_download', id);
                    else if (action === 'cancel') await this.call('cancel_download', id);
                    else if (action === 'del') await this.call('remove_download', id);
                    else if (action === 'open') await this.call('open_external', btn.dataset.path);
                    setTimeout(() => this.loadDownloads(), 300);
                });
            });

            list.appendChild(item);
        });
    },

    onDownloadProgress(data) {
        // 实时更新下载列表 (如果可见)
        const view = document.getElementById('view-downloads');
        if (view && view.classList.contains('active')) {
            // 节流: 每500ms刷新一次
            if (!this._dlUpdateTimer) {
                this._dlUpdateTimer = setTimeout(() => {
                    this.loadDownloads();
                    this._dlUpdateTimer = null;
                }, 500);
            }
        }
        // 更新下载徽章
        if (data.status === 'completed') {
            this.toast(`下载完成: ${data.id}`, 'success');
        } else if (data.status === 'failed') {
            this.toast(`下载失败: ${data.id}`, 'error');
        }
    },

    startDownloadTimer() {
        if (this.downloadTimer) clearInterval(this.downloadTimer);
        this.downloadTimer = setInterval(() => {
            const view = document.getElementById('view-downloads');
            if (view && view.classList.contains('active')) {
                this.loadDownloads();
            }
        }, 3000);
    },

    // ======== 批量下载 ========

    showBatchDownload(vodName, episodes) {
        const modal = document.getElementById('batch-download-modal');
        const list = document.getElementById('batch-episode-list');
        list.innerHTML = '';
        this._batchVodName = vodName;
        this._batchEpisodes = episodes;

        episodes.forEach((ep, i) => {
            const item = document.createElement('div');
            item.className = 'batch-episode-item';
            item.innerHTML = `
                <input type="checkbox" checked data-index="${i}">
                <span>${this.escape(ep.name || `第${i+1}集`)}</span>
            `;
            list.appendChild(item);
        });

        modal.classList.remove('hidden');
    },

    async startBatchDownload() {
        const checkboxes = document.querySelectorAll('#batch-episode-list input[type="checkbox"]:checked');
        const selected = [];
        checkboxes.forEach(cb => {
            const idx = parseInt(cb.dataset.index);
            if (this._batchEpisodes[idx]) {
                selected.push({
                    name: this._batchEpisodes[idx].name || `第${idx+1}集`,
                    url: this._batchEpisodes[idx].url,
                });
            }
        });

        if (selected.length === 0) {
            this.toast('请选择要下载的集数', 'error');
            return;
        }

        const result = await this.call('add_batch_download', this._batchVodName, JSON.stringify(selected));
        if (result.ok) {
            this.toast(`已添加 ${selected.length} 个下载任务`, 'success');
            document.getElementById('batch-download-modal').classList.add('hidden');
            this.switchView('downloads');
            this.loadDownloads();
        } else {
            this.toast('批量下载失败', 'error');
        }
    },

    // ======== AB回放 & 循环播放 ========

    abRepeatA: -1,
    abRepeatB: -1,
    loopMode: 0, // 0=不循环, 1=单集循环, 2=列表循环

    toggleABRepeat() {
        const video = document.getElementById('video-player');
        if (!video) return;

        if (this.abRepeatA < 0) {
            // 设置A点
            this.abRepeatA = video.currentTime;
            this.showVideoInfo(`A点: ${this.formatDuration(this.abRepeatA)}`);
            document.getElementById('ab-repeat-indicator').classList.remove('hidden');
            this.updateABRepeatInfo();
        } else if (this.abRepeatB < 0) {
            // 设置B点
            this.abRepeatB = video.currentTime;
            if (this.abRepeatB <= this.abRepeatA) {
                this.toast('B点必须在A点之后', 'error');
                this.abRepeatB = -1;
                return;
            }
            this.showVideoInfo(`B点: ${this.formatDuration(this.abRepeatB)}`);
            this.updateABRepeatInfo();
        } else {
            // 清除AB回放
            this.abRepeatA = -1;
            this.abRepeatB = -1;
            document.getElementById('ab-repeat-indicator').classList.add('hidden');
            this.showVideoInfo('AB回放已取消');
        }
    },

    updateABRepeatInfo() {
        const info = document.getElementById('ab-repeat-info');
        if (info) {
            const aText = this.abRepeatA >= 0 ? this.formatDuration(this.abRepeatA) : '--:--';
            const bText = this.abRepeatB >= 0 ? this.formatDuration(this.abRepeatB) : '--:--';
            info.textContent = `A: ${aText} / B: ${bText}`;
        }
    },

    checkABRepeat() {
        const video = document.getElementById('video-player');
        if (!video || this.abRepeatA < 0 || this.abRepeatB < 0) return;
        if (video.currentTime >= this.abRepeatB) {
            video.currentTime = this.abRepeatA;
        }
    },

    toggleLoop() {
        this.loopMode = (this.loopMode + 1) % 3;
        const modes = ['不循环', '单集循环', '列表循环'];
        const indicator = document.getElementById('loop-indicator');
        const text = document.getElementById('loop-mode-text');

        if (this.loopMode > 0) {
            indicator.classList.remove('hidden');
            text.textContent = modes[this.loopMode];
        } else {
            indicator.classList.add('hidden');
        }

        this.showVideoInfo(modes[this.loopMode]);
    },

    // ======== 系统设置恢复 ========

    async restoreSystemSettings() {
        // 开机自启
        const autostart = await this.call('is_autostart_enabled');
        const autostartToggle = document.getElementById('autostart-toggle');
        if (autostartToggle) autostartToggle.checked = autostart.enabled;

        // 最小化到托盘
        const minimizeTray = await this.call('get_setting', 'minimizeToTray', '0');
        const minimizeToggle = document.getElementById('minimize-tray-toggle');
        if (minimizeToggle) minimizeToggle.checked = minimizeTray === '1';

        // 下载限速
        const speedLimit = await this.call('get_download_speed_limit');
        const speedInput = document.getElementById('download-speed-limit');
        if (speedInput && speedLimit.limit_kbps > 0) speedInput.value = speedLimit.limit_kbps;

        // 下载线程
        const threads = await this.call('get_setting', 'downloadThreads', '4');
        const threadsSelect = document.getElementById('download-threads');
        if (threadsSelect) threadsSelect.value = threads;
    },
};

// ======== 启动 ========

window.addEventListener('DOMContentLoaded', () => {
    App.init();
});

// 点击背景关闭弹窗
document.addEventListener('click', e => {
    if (e.target.classList.contains('modal-backdrop')) {
        const modal = e.target.closest('.modal');
        if (modal) {
            if (modal.id === 'player-modal') App.closePlayer();
            else modal.classList.add('hidden');
        }
    }
});
