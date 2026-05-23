/**
 * 注册页面 JavaScript
 * 使用 utils.js 中的工具库
 */

// 状态
let currentTask = null;
let currentBatch = null;
let currentBatchMode = 'registration';
let logPollingInterval = null;
let batchPollingInterval = null;
let accountsPollingInterval = null;
let isBatchMode = false;
let taskCompleted = false;  // 标记任务是否已完成
let batchCompleted = false;  // 标记批量任务是否已完成
let taskFinalStatus = null;  // 保存任务的最终状态
let batchFinalStatus = null;  // 保存批量任务的最终状态
let displayedLogs = new Set();  // 用于日志去重
let toastShown = false;  // 标记是否已显示过 toast
let availableServices = {
    tempmail: { available: true, services: [] },
    moe_mail: { available: false, services: [] },
    temp_mail: { available: false, services: [] },
    duck_mail: { available: false, services: [] },
    freemail: { available: false, services: [] },
    cloud_mail: { available: false, services: [] },
    cloudflare_temp_email: { available: false, services: [] },
    imap_mail: { available: false, services: [] }
};

// WebSocket 相关变量
let webSocket = null;
let batchWebSocket = null;  // 批量任务 WebSocket
let useWebSocket = true;  // 是否使用 WebSocket
let wsHeartbeatInterval = null;  // 心跳定时器
let batchWsHeartbeatInterval = null;  // 批量任务心跳定时器
let activeTaskUuid = null;   // 当前活跃的单任务 UUID（用于页面重新可见时重连）
let activeBatchId = null;    // 当前活跃的批量任务 ID（用于页面重新可见时重连）

// DOM 元素
const elements = {
    form: document.getElementById('registration-form'),
    emailService: document.getElementById('email-service'),
    regMode: document.getElementById('reg-mode'),
    regModeGroup: document.getElementById('reg-mode-group'),
    batchCountGroup: document.getElementById('batch-count-group'),
    batchCount: document.getElementById('batch-count'),
    batchOptions: document.getElementById('batch-options'),
    intervalMin: document.getElementById('interval-min'),
    intervalMax: document.getElementById('interval-max'),
    startBtn: document.getElementById('start-btn'),
    cancelBtn: document.getElementById('cancel-btn'),
    taskStatusRow: document.getElementById('task-status-row'),
    batchProgressSection: document.getElementById('batch-progress-section'),
    consoleLog: document.getElementById('console-log'),
    clearLogBtn: document.getElementById('clear-log-btn'),
    // 任务状态
    taskId: document.getElementById('task-id'),
    taskEmail: document.getElementById('task-email'),
    taskStatus: document.getElementById('task-status'),
    taskService: document.getElementById('task-service'),
    taskStatusBadge: document.getElementById('task-status-badge'),
    // 批量状态
    batchProgressText: document.getElementById('batch-progress-text'),
    batchProgressPercent: document.getElementById('batch-progress-percent'),
    progressBar: document.getElementById('progress-bar'),
    batchSuccess: document.getElementById('batch-success'),
    batchFailed: document.getElementById('batch-failed'),
    batchRemaining: document.getElementById('batch-remaining'),
    // 已注册账号
    recentAccountsTable: document.getElementById('recent-accounts-table'),
    refreshAccountsBtn: document.getElementById('refresh-accounts-btn'),
    // 监听任务选择器
    monitorTaskSelector: document.getElementById('monitor-task-selector'),
    // 任务选择
    monitorTaskSelector: document.getElementById('monitor-task-selector'),
    // 监听任务选择器
    monitorTaskSelector: document.getElementById('monitor-task-selector'),
    // 批量并发控件
    concurrencyMode: document.getElementById('concurrency-mode'),
    concurrencyCountGroup: document.getElementById('concurrency-count-group'),
    concurrencyCount: document.getElementById('concurrency-count'),
    intervalGroup: document.getElementById('interval-group'),
    // 注册后自动操作
    autoUploadCpa: document.getElementById('auto-upload-cpa'),
    cpaServiceSelectGroup: document.getElementById('cpa-service-select-group'),
    cpaServiceSelect: document.getElementById('cpa-service-select'),
    autoUploadSub2api: document.getElementById('auto-upload-sub2api'),
    sub2apiServiceSelectGroup: document.getElementById('sub2api-service-select-group'),
    sub2apiServiceSelect: document.getElementById('sub2api-service-select'),
    autoUploadTm: document.getElementById('auto-upload-tm'),
    tmServiceSelectGroup: document.getElementById('tm-service-select-group'),
    tmServiceSelect: document.getElementById('tm-service-select'),
    // 邮箱服务选择模态框
    emailServiceTrigger: document.getElementById('email-service-trigger'),
    emailServiceDisplay: document.getElementById('email-service-display'),
    emailServiceModal: document.getElementById('email-service-modal'),
    emailServiceList: document.getElementById('email-service-list'),
    closeEmailModal: document.getElementById('close-email-modal'),
};

// 初始化
document.addEventListener('DOMContentLoaded', () => {
    initEventListeners();
    loadAvailableServices();
    loadRecentAccounts();
    startAccountsPolling();
    initVisibilityReconnect();
    restoreActiveTask();
    initAutoUploadOptions();
    startActiveTasksPolling();
});

// 初始化注册后自动操作选项（CPA / Sub2API / TM）
async function initAutoUploadOptions() {
    await Promise.all([
        loadServiceSelect('/cpa-services?enabled=true', elements.cpaServiceSelect, elements.autoUploadCpa, elements.cpaServiceSelectGroup),
        loadServiceSelect('/sub2api-services?enabled=true', elements.sub2apiServiceSelect, elements.autoUploadSub2api, elements.sub2apiServiceSelectGroup),
        loadServiceSelect('/tm-services?enabled=true', elements.tmServiceSelect, elements.autoUploadTm, elements.tmServiceSelectGroup),
    ]);
}

// 通用：构建自定义多选下拉组件并处理联动
async function loadServiceSelect(apiPath, container, checkbox, selectGroup) {
    if (!checkbox || !container) return;
    let services = [];
    try {
        services = await api.get(apiPath);
    } catch (e) { }

    if (!services || services.length === 0) {
        checkbox.disabled = true;
        checkbox.title = '请先在设置中添加对应服务';
        const label = checkbox.closest('label');
        if (label) label.style.opacity = '0.5';
        container.innerHTML = '<div class="msd-empty">暂无可用服务</div>';
    } else {
        const items = services.map(s =>
            `<label class="msd-item">
                <input type="checkbox" value="${s.id}">
                <span>${escapeHtml(s.name)}</span>
            </label>`
        ).join('');
        container.innerHTML = `
            <div class="msd-dropdown" id="${container.id}-dd">
                <div class="msd-trigger" onclick="toggleMsd('${container.id}-dd')">
                    <span class="msd-label">全部 (${services.length})</span>
                    <span class="msd-arrow">▼</span>
                </div>
                <div class="msd-list">${items}</div>
            </div>`;
        // 监听 checkbox 变化，更新触发器文字
        container.querySelectorAll('.msd-item input').forEach(cb => {
            cb.addEventListener('change', () => updateMsdLabel(container.id + '-dd'));
        });
        // 点击外部关闭
        document.addEventListener('click', (e) => {
            const dd = document.getElementById(container.id + '-dd');
            if (dd && !dd.contains(e.target)) dd.classList.remove('open');
        }, true);
    }

    // 联动显示/隐藏服务选择区
    checkbox.addEventListener('change', () => {
        if (selectGroup) selectGroup.style.display = checkbox.checked ? 'block' : 'none';
    });
}

function toggleMsd(ddId) {
    const dd = document.getElementById(ddId);
    if (dd) dd.classList.toggle('open');
}

function updateMsdLabel(ddId) {
    const dd = document.getElementById(ddId);
    if (!dd) return;
    const all = dd.querySelectorAll('.msd-item input');
    const checked = dd.querySelectorAll('.msd-item input:checked');
    const label = dd.querySelector('.msd-label');
    if (!label) return;
    if (checked.length === 0) label.textContent = '点击选择目标';
    else if (checked.length === all.length) label.textContent = `全部 (${all.length})`;
    else label.textContent = Array.from(checked).map(c => c.nextElementSibling.textContent).join(', ');
}

// 获取自定义多选下拉中选中的服务 ID 列表
function getSelectedServiceIds(container) {
    if (!container) return [];
    return Array.from(container.querySelectorAll('.msd-item input:checked')).map(cb => parseInt(cb.value));
}

// 事件监听
function initEventListeners() {
    // 注册表单提交
    elements.form.addEventListener('submit', handleStartRegistration);

    // 注册模式切换
    elements.regMode.addEventListener('change', handleModeChange);

    // 邮箱服务触发器
    elements.emailServiceTrigger.addEventListener('click', openEmailServiceModal);
    elements.closeEmailModal.addEventListener('click', () => elements.emailServiceModal.classList.remove('active'));
    elements.emailServiceModal.addEventListener('click', (e) => {
        if (e.target === elements.emailServiceModal) elements.emailServiceModal.classList.remove('active');
    });

    // 取消按钮
    elements.cancelBtn.addEventListener('click', handleCancelTask);

    // 清空日志
    elements.clearLogBtn.addEventListener('click', () => {
        elements.consoleLog.innerHTML = '<div class="log-line info">系统: 日志已清空</div>';
        displayedLogs.clear();  // 清空日志去重集合
    });

    // 刷新账号列表
    elements.refreshAccountsBtn.addEventListener('click', () => {
        loadRecentAccounts();
    });

    elements.concurrencyMode.addEventListener('change', () => {
        handleConcurrencyModeChange(elements.concurrencyMode, elements.concurrencyCountGroup, elements.intervalGroup);
    });
    // 活跃任务监听切换
    elements.monitorTaskSelector.addEventListener('change', async (e) => {
        const val = e.target.value;
        if (!val) return;

        const [type, id, mode] = val.split(':');

        // 断开现有连接
        if (webSocket) { webSocket.close(); webSocket = null; }
        if (batchWebSocket) { batchWebSocket.close(); batchWebSocket = null; }

        // 清空 UI
        elements.consoleLog.innerHTML = `<div class="log-line info">[系统] 正在准备监听任务 ${id.substring(0, 8)}...</div>`;
        displayedLogs.clear();

        if (type === 'batch') {
            activeBatchId = id;
            batchCompleted = false;
            currentBatchMode = mode || 'registration';

            // 立即获取并同步当前状态到 UI
            try {
                const data = await api.get(`/registration/batch/${id}`);

                showBatchStatus({ count: data.total });
                updateBatchProgress(data);
                addLog('info', `[系统] 已同步活跃批量任务状态: ${data.success} 成功, ${data.failed} 失败`);
            } catch (err) {
                console.error('获取同步任务状态失败:', err);
            }

            connectBatchWebSocket(id);
        } else {
            activeTaskUuid = id;
            taskCompleted = false;

            try {
                const data = await api.get(`/registration/tasks/${id}`);
                showTaskStatus(data, data.email_service || '-');
                updateTaskStatus(data.status);
            } catch (err) { }

            connectWebSocket(id);
        }
    });
}

// 打开邮箱服务选择模态框
function openEmailServiceModal() {
    const list = elements.emailServiceList;
    list.innerHTML = '';

    // 辅助函数：创建服务卡片
    const createCard = (id, name, type, desc, icon = '📧') => {
        const card = document.createElement('div');
        card.className = 'service-card';
        if (elements.emailService.value === id) card.classList.add('active');

        card.innerHTML = `
            <div class="service-card-badge">已选</div>
            <div style="font-size: 24px;">${icon}</div>
            <div class="service-card-title">${name}</div>
            <div class="service-card-desc">${desc}</div>
        `;

        card.onclick = () => {
            selectService(id, name);
            elements.emailServiceModal.classList.remove('active');
        };
        return card;
    };

    // 1. Tempmail
    if (availableServices.tempmail.available) {
        availableServices.tempmail.services.forEach(s => {
            list.appendChild(createCard(`tempmail:${s.id || 'default'}`, s.name, 'tempmail', '由 Tempmail.lol 提供的免费临时邮箱', '🌐'));
        });
    }

    // 其他服务
    const otherServices = [
        { key: 'cloud_mail', name: 'CloudMail', desc: 'Cloudflare Email 路由', icon: '🌫️' },
        { key: 'freemail', name: 'Freemail', desc: 'Cloudflare Workers 临时邮箱', icon: '📬' },
        { key: 'cloudflare_temp_email', name: 'Cloudflare Temp Email', desc: 'Cloudflare Worker 邮箱 JWT 模式', icon: '☁️' },
        { key: 'imap_mail', name: 'IMAP Catch-all', desc: 'Cloudflare Email Routing 转发到 Gmail/Outlook IMAP', icon: 'IMAP' }
    ];

    otherServices.forEach(os => {
        if (availableServices[os.key] && availableServices[os.key].available) {
            availableServices[os.key].services.forEach(s => {
                list.appendChild(createCard(`${os.key}:${s.id}`, s.name, os.key, os.desc, os.icon));
            });
        }
    });

    elements.emailServiceModal.classList.add('active');
}

// 选择服务并更新 UI
function selectService(value, name) {
    elements.emailService.value = value;
    elements.emailServiceDisplay.textContent = name;
    saveEmailServiceSelection(value);

    // 触发原始的切换逻辑
    handleServiceChange({ target: { value } });
}

const EMAIL_SERVICE_STORAGE_KEY = 'registrationEmailService';
const CONFIGURED_EMAIL_SERVICE_PRIORITY = ['imap_mail', 'cloudflare_temp_email', 'freemail', 'cloud_mail'];

function loadSavedEmailServiceSelection() {
    try {
        return localStorage.getItem(EMAIL_SERVICE_STORAGE_KEY);
    } catch (error) {
        return null;
    }
}

function saveEmailServiceSelection(value) {
    try {
        localStorage.setItem(EMAIL_SERVICE_STORAGE_KEY, value);
    } catch (error) { }
}

function findEmailServiceSelection(value, services = availableServices) {
    if (!value) return null;

    const [type, id] = value.split(':');
    if (type === 'tempmail' && id === 'default' && services.tempmail && services.tempmail.available) {
        return { value, name: 'Tempmail.lol (自动)' };
    }

    const group = services[type];
    if (!group || !group.available || !Array.isArray(group.services)) return null;

    const service = group.services.find(s => String(s.id) === id);
    if (!service) return null;

    return { value, name: service.name };
}

function getDefaultEmailServiceSelection(services) {
    const savedSelection = findEmailServiceSelection(loadSavedEmailServiceSelection(), services);
    if (savedSelection) return savedSelection;

    for (const type of CONFIGURED_EMAIL_SERVICE_PRIORITY) {
        const group = services[type];
        if (group && group.available && Array.isArray(group.services) && group.services.length > 0) {
            const service = group.services[0];
            return { value: `${type}:${service.id}`, name: service.name };
        }
    }

    return { value: 'tempmail:default', name: 'Tempmail.lol (自动)' };
}

// 加载可用的邮箱服务
async function loadAvailableServices() {
    try {
        const data = await api.get('/registration/available-services');
        availableServices = data;

        const defaultSelection = getDefaultEmailServiceSelection(data);
        selectService(defaultSelection.value, defaultSelection.name);

        addLog('info', '[系统] 邮箱服务列表已加载');
    } catch (error) {
        console.error('加载邮箱服务列表失败:', error);
        addLog('warning', '[警告] 加载邮箱服务列表失败');
    }
}

// 更新邮箱服务选择框（旧方法，保留逻辑但不填充 select）
function updateEmailServiceOptions() {
    // 该方法现在仅用于初始化可用服务状态，不操作 DOM select
}

// 处理邮箱服务切换
function handleServiceChange(e) {
    const value = e.target.value;
    if (!value) return;

    const [type, id] = value.split(':');
    elements.regModeGroup.style.display = 'block';

    // 显示服务信息
    if (type === 'moe_mail') {
        const service = availableServices.moe_mail.services.find(s => s.id == id);
        if (service) {
            addLog('info', `[系统] 已选择自定义域名服务: ${service.name}`);
        }
    } else if (type === 'temp_mail') {
        const service = availableServices.temp_mail.services.find(s => s.id == id);
        if (service) {
            addLog('info', `[系统] 已选择 Temp-Mail 自部署服务: ${service.name}`);
        }
    } else if (type === 'duck_mail') {
        const service = availableServices.duck_mail.services.find(s => s.id == id);
        if (service) {
            addLog('info', `[系统] 已选择 DuckMail 服务: ${service.name}`);
        }
    } else if (type === 'freemail') {
        const service = availableServices.freemail.services.find(s => s.id == id);
        if (service) {
            addLog('info', `[系统] 已选择 Freemail 服务: ${service.name}`);
        }
    } else if (type === 'cloud_mail') {
        const service = availableServices.cloud_mail.services.find(s => s.id == id);
        if (service) {
            addLog('info', `[系统] 已选择 CloudMail 服务: ${service.name}`);
        }
    } else if (type === 'cloudflare_temp_email') {
        const service = availableServices.cloudflare_temp_email.services.find(s => s.id == id);
        if (service) {
            addLog('info', `[系统] 已选择 Cloudflare Temp Email 服务: ${service.name}`);
        }
    } else if (type === 'imap_mail') {
        const service = availableServices.imap_mail.services.find(s => s.id == id);
        if (service) {
            addLog('info', `[系统] 已选择 IMAP Catch-all 服务: ${service.name}`);
        }
    }
}

// 模式切换
function handleModeChange(e) {
    const mode = e.target.value;
    isBatchMode = mode === 'batch';

    elements.batchCountGroup.style.display = isBatchMode ? 'block' : 'none';
    elements.batchOptions.style.display = isBatchMode ? 'block' : 'none';
}

// 并发模式切换（批量）
function handleConcurrencyModeChange(selectEl, countGroupEl, intervalGroupEl) {
    const mode = selectEl.value;
    if (mode === 'parallel') {
        // 多线程并行
        if (countGroupEl) countGroupEl.style.display = 'block';
        if (intervalGroupEl) intervalGroupEl.style.display = 'none';
    } else {
        // 单线程串行
        if (countGroupEl) countGroupEl.style.display = 'none';
        if (intervalGroupEl) intervalGroupEl.style.display = 'block';
    }
}

// 开始注册
async function handleStartRegistration(e) {
    e.preventDefault();

    const selectedValue = elements.emailService.value;
    if (!selectedValue) {
        toast.error('请选择一个邮箱服务');
        return;
    }

    const [emailServiceType, serviceId] = selectedValue.split(':');

    // 禁用开始按钮
    elements.startBtn.disabled = true;
    elements.cancelBtn.disabled = false;

    // 清空日志
    elements.consoleLog.innerHTML = '';

    // 构建请求数据（代理从设置中自动获取）
    const requestData = {
        email_service_type: emailServiceType,
        auto_upload_cpa: elements.autoUploadCpa ? elements.autoUploadCpa.checked : false,
        cpa_service_ids: elements.autoUploadCpa && elements.autoUploadCpa.checked ? getSelectedServiceIds(elements.cpaServiceSelect) : [],
        auto_upload_sub2api: elements.autoUploadSub2api ? elements.autoUploadSub2api.checked : false,
        sub2api_service_ids: elements.autoUploadSub2api && elements.autoUploadSub2api.checked ? getSelectedServiceIds(elements.sub2apiServiceSelect) : [],
        auto_upload_tm: elements.autoUploadTm ? elements.autoUploadTm.checked : false,
        tm_service_ids: elements.autoUploadTm && elements.autoUploadTm.checked ? getSelectedServiceIds(elements.tmServiceSelect) : [],
    };

    // 如果选择了数据库中的服务，传递 service_id
    if (serviceId && serviceId !== 'default') {
        requestData.email_service_id = parseInt(serviceId);
    }

    if (isBatchMode) {
        await handleBatchRegistration(requestData);
    } else {
        await handleSingleRegistration(requestData);
    }
}

// 单次注册
async function handleSingleRegistration(requestData) {
    // 重置任务状态
    taskCompleted = false;
    taskFinalStatus = null;
    displayedLogs.clear();  // 清空日志去重集合
    toastShown = false;  // 重置 toast 标志

    addLog('info', '[系统] 正在启动注册任务...');

    try {
        const data = await api.post('/registration/start', requestData);

        currentTask = data;
        activeTaskUuid = data.task_uuid;  // 保存用于重连
        // 持久化到 sessionStorage，跨页面导航后可恢复
        sessionStorage.setItem('activeTask', JSON.stringify({ task_uuid: data.task_uuid, mode: 'single' }));
        addLog('info', `[系统] 任务已创建: ${data.task_uuid}`);
        showTaskStatus(data, elements.emailServiceDisplay.textContent);
        updateTaskStatus('running');

        // 优先使用 WebSocket
        connectWebSocket(data.task_uuid);

    } catch (error) {
        addLog('error', `[错误] 启动失败: ${error.message}`);
        toast.error(error.message);
        resetButtons();
    }
}


// ============== WebSocket 功能 ==============

// 连接 WebSocket
function connectWebSocket(taskUuid) {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/api/ws/task/${taskUuid}`;

    try {
        webSocket = new WebSocket(wsUrl);

        webSocket.onopen = () => {
            console.log('WebSocket 连接成功');
            useWebSocket = true;
            // 停止轮询（如果有）
            stopLogPolling();
            // 开始心跳
            startWebSocketHeartbeat();
        };

        webSocket.onmessage = (event) => {
            const data = JSON.parse(event.data);

            if (data.type === 'log') {
                const logType = getLogType(data.message);
                addLog(logType, data.message);
            } else if (data.type === 'status') {
                updateTaskStatus(data.status);

                // 更新邮箱和服务（如果返回了这些信息）
                if (data.email) elements.taskEmail.textContent = data.email;
                if (data.email_service) elements.taskService.textContent = data.email_service;

                // 检查是否完成
                if (['completed', 'failed', 'cancelled', 'cancelling'].includes(data.status)) {
                    // 保存最终状态，用于 onclose 判断
                    taskFinalStatus = data.status;
                    taskCompleted = true;

                    // 断开 WebSocket（异步操作）
                    disconnectWebSocket();

                    // 任务完成后再重置按钮
                    resetButtons();

                    // 只显示一次 toast
                    if (!toastShown) {
                        toastShown = true;
                        if (data.status === 'completed') {
                            addLog('success', '[成功] 注册成功！');
                            toast.success('注册成功！');
                            // 刷新账号列表
                            loadRecentAccounts();
                        } else if (data.status === 'failed') {
                            addLog('error', '[错误] 注册失败');
                            toast.error('注册失败');
                        } else if (data.status === 'cancelled' || data.status === 'cancelling') {
                            addLog('warning', '[警告] 任务已取消');
                        }
                    }
                }
            } else if (data.type === 'pong') {
                // 心跳响应，忽略
            }
        };

        webSocket.onclose = (event) => {
            console.log('WebSocket 连接关闭:', event.code);
            stopWebSocketHeartbeat();

            // 只有在任务未完成且最终状态不是完成状态时才切换到轮询
            // 使用 taskFinalStatus 而不是 currentTask.status，因为 currentTask 可能已被重置
            const shouldPoll = !taskCompleted &&
                taskFinalStatus === null;  // 如果 taskFinalStatus 有值，说明任务已完成

            if (shouldPoll && currentTask) {
                console.log('切换到轮询模式');
                useWebSocket = false;
                startLogPolling(currentTask.task_uuid);
            }
        };

        webSocket.onerror = (error) => {
            console.error('WebSocket 错误:', error);
            // 切换到轮询
            useWebSocket = false;
            stopWebSocketHeartbeat();
            startLogPolling(taskUuid);
        };

    } catch (error) {
        console.error('WebSocket 连接失败:', error);
        useWebSocket = false;
        startLogPolling(taskUuid);
    }
}

// 断开 WebSocket
function disconnectWebSocket() {
    stopWebSocketHeartbeat();
    if (webSocket) {
        webSocket.close();
        webSocket = null;
    }
}

// 开始心跳
function startWebSocketHeartbeat() {
    stopWebSocketHeartbeat();
    wsHeartbeatInterval = setInterval(() => {
        if (webSocket && webSocket.readyState === WebSocket.OPEN) {
            webSocket.send(JSON.stringify({ type: 'ping' }));
        }
    }, 25000);  // 每 25 秒发送一次心跳
}

// 停止心跳
function stopWebSocketHeartbeat() {
    if (wsHeartbeatInterval) {
        clearInterval(wsHeartbeatInterval);
        wsHeartbeatInterval = null;
    }
}

// 发送取消请求
function cancelViaWebSocket() {
    if (webSocket && webSocket.readyState === WebSocket.OPEN) {
        webSocket.send(JSON.stringify({ type: 'cancel' }));
    }
}

// 批量注册
async function handleBatchRegistration(requestData) {
    // 重置批量任务状态
    batchCompleted = false;
    batchFinalStatus = null;
    displayedLogs.clear();  // 清空日志去重集合
    toastShown = false;  // 重置 toast 标志

    const count = parseInt(elements.batchCount.value) || 5;
    const intervalMin = parseInt(elements.intervalMin.value) || 5;
    const intervalMax = parseInt(elements.intervalMax.value) || 30;
    const concurrency = parseInt(elements.concurrencyCount.value) || 3;
    const mode = elements.concurrencyMode.value || 'pipeline';

    requestData.count = count;
    requestData.interval_min = intervalMin;
    requestData.interval_max = intervalMax;
    requestData.concurrency = mode === 'parallel' ? Math.min(50, Math.max(1, concurrency)) : 1;
    requestData.mode = mode;

    addLog('info', `[系统] 正在启动批量注册任务 (数量: ${count})...`);

    try {
        const data = await api.post('/registration/batch', requestData);

        currentBatch = data;
        currentBatchMode = 'registration';
        activeBatchId = data.batch_id;  // 保存用于重连
        // 持久化到 sessionStorage，跨页面导航后可恢复
        sessionStorage.setItem('activeTask', JSON.stringify({ batch_id: data.batch_id, mode: 'batch', total: data.count }));
        addLog('info', `[系统] 批量任务已创建: ${data.batch_id}`);
        addLog('info', `[系统] 共 ${data.count} 个任务已加入队列`);
        showBatchStatus(data);

        // 优先使用 WebSocket
        connectBatchWebSocket(data.batch_id);

    } catch (error) {
        addLog('error', `[错误] 启动失败: ${error.message}`);
        toast.error(error.message);
        resetButtons();
    }
}

// 取消任务
async function handleCancelTask() {
    // 禁用取消按钮，防止重复点击
    elements.cancelBtn.disabled = true;
    addLog('info', '[系统] 正在提交取消请求...');

    try {
        // 批量任务取消
        if (currentBatch && isBatchMode) {
            // 优先通过 WebSocket 取消
            if (batchWebSocket && batchWebSocket.readyState === WebSocket.OPEN) {
                batchWebSocket.send(JSON.stringify({ type: 'cancel' }));
                addLog('warning', '[警告] 批量任务取消请求已提交');
                toast.info('任务取消请求已提交');
            } else {
                await api.post(`/registration/batch/${currentBatch.batch_id}/cancel`);
                addLog('warning', '[警告] 批量任务取消请求已提交');
                toast.info('任务取消请求已提交');
                stopBatchPolling();
                resetButtons();
            }
        }
        // 单次任务取消
        else if (currentTask) {
            // 优先通过 WebSocket 取消
            if (webSocket && webSocket.readyState === WebSocket.OPEN) {
                webSocket.send(JSON.stringify({ type: 'cancel' }));
                addLog('warning', '[警告] 任务取消请求已提交');
                toast.info('任务取消请求已提交');
            } else {
                // 降级到 REST API
                await api.post(`/registration/tasks/${currentTask.task_uuid}/cancel`);
                addLog('warning', '[警告] 任务已取消');
                toast.info('任务已取消');
                stopLogPolling();
                resetButtons();
            }
        }
        // 没有活动任务
        else {
            addLog('warning', '[警告] 没有活动的任务可以取消');
            toast.warning('没有活动的任务');
            resetButtons();
        }
    } catch (error) {
        addLog('error', `[错误] 取消失败: ${error.message}`);
        toast.error(error.message);
        // 恢复取消按钮，允许重试
        elements.cancelBtn.disabled = false;
    }
}

// 开始轮询日志
function startLogPolling(taskUuid) {
    let lastLogIndex = 0;

    logPollingInterval = setInterval(async () => {
        try {
            const data = await api.get(`/registration/tasks/${taskUuid}/logs`);

            // 更新任务状态
            updateTaskStatus(data.status);

            // 更新邮箱信息
            if (data.email) {
                elements.taskEmail.textContent = data.email;
            }
            if (data.email_service) {
                elements.taskService.textContent = getServiceTypeText(data.email_service);
            }

            // 添加新日志
            const logs = data.logs || [];
            for (let i = lastLogIndex; i < logs.length; i++) {
                const log = logs[i];
                const logType = getLogType(log);
                addLog(logType, log);
            }
            lastLogIndex = logs.length;

            // 检查任务是否完成
            if (['completed', 'failed', 'cancelled'].includes(data.status)) {
                stopLogPolling();
                resetButtons();

                // 只显示一次 toast
                if (!toastShown) {
                    toastShown = true;
                    if (data.status === 'completed') {
                        addLog('success', '[成功] 注册成功！');
                        toast.success('注册成功！');
                        // 刷新账号列表
                        loadRecentAccounts();
                    } else if (data.status === 'failed') {
                        addLog('error', '[错误] 注册失败');
                        toast.error('注册失败');
                    } else if (data.status === 'cancelled') {
                        addLog('warning', '[警告] 任务已取消');
                    }
                }
            }
        } catch (error) {
            console.error('轮询日志失败:', error);
        }
    }, 1000);
}

// 停止轮询日志
function stopLogPolling() {
    if (logPollingInterval) {
        clearInterval(logPollingInterval);
        logPollingInterval = null;
    }
}

// 开始轮询批量状态（WebSocket 不可用时的降级方案，支持增量日志）
function startBatchPolling(batchId) {
    let logOffset = 0;  // 已接收的日志偏移量

    addLog('info', '[系统] WebSocket 不可用，已切换为后台轮询模式，任务继续在服务器运行中...');

    batchPollingInterval = setInterval(async () => {
        try {
            const data = await api.get(`/registration/batch/${batchId}/logs?offset=${logOffset}`);

            // 更新进度
            updateBatchProgress(data);

            // 追加增量日志
            if (data.logs && data.logs.length > 0) {
                data.logs.forEach(log => {
                    const logType = getLogType(log);
                    addLog(logType, log);
                });
                logOffset = data.log_offset;  // 更新偏移量
            }

            // 检查是否完成
            if (data.finished) {
                stopBatchPolling();
                resetButtons();

                // 只显示一次 toast
                if (!toastShown) {
                    toastShown = true;
                    addLog('info', `[完成] 批量任务完成！成功: ${data.success}, 失败: ${data.failed}`);
                    if (data.success > 0) {
                        toast.success(`批量注册完成，成功 ${data.success} 个`);
                        loadRecentAccounts();
                    } else {
                        toast.warning('批量注册完成，但没有成功注册任何账号');
                    }
                }
            }
        } catch (error) {
            console.error('轮询批量状态失败:', error);
        }
    }, 2000);
}

// 停止轮询批量状态
function stopBatchPolling() {
    if (batchPollingInterval) {
        clearInterval(batchPollingInterval);
        batchPollingInterval = null;
    }
}

// 显示任务状态
function showTaskStatus(task, serviceName = '-') {
    elements.taskStatusRow.style.display = 'grid';
    elements.batchProgressSection.style.display = 'none';
    elements.taskStatusBadge.style.display = 'inline-flex';
    elements.taskId.textContent = task.task_uuid.substring(0, 8) + '...';
    elements.taskEmail.textContent = '-';
    elements.taskService.textContent = serviceName;
}

// 更新任务状态
function updateTaskStatus(status) {
    const statusInfo = {
        pending: { text: '等待中', class: 'pending' },
        running: { text: '运行中', class: 'running' },
        completed: { text: '已完成', class: 'completed' },
        failed: { text: '失败', class: 'failed' },
        cancelled: { text: '已取消', class: 'disabled' }
    };

    const info = statusInfo[status] || { text: status, class: '' };
    elements.taskStatusBadge.textContent = info.text;
    elements.taskStatusBadge.className = `status-badge ${info.class}`;
    elements.taskStatus.textContent = info.text;
}

// 显示批量状态
function showBatchStatus(batch) {
    elements.batchProgressSection.style.display = 'block';
    elements.taskStatusRow.style.display = 'none';
    elements.taskStatusBadge.style.display = 'none';
    elements.batchProgressText.textContent = `0/${batch.count}`;
    elements.batchProgressPercent.textContent = '0%';
    elements.progressBar.style.width = '0%';
    elements.batchSuccess.textContent = '0';
    elements.batchFailed.textContent = '0';
    elements.batchRemaining.textContent = batch.count;

    // 重置计数器
    elements.batchSuccess.dataset.last = '0';
    elements.batchFailed.dataset.last = '0';
}

// 更新批量进度
function updateBatchProgress(data) {
    const progress = ((data.completed / data.total) * 100).toFixed(0);
    elements.batchProgressText.textContent = `${data.completed}/${data.total}`;
    elements.batchProgressPercent.textContent = `${progress}%`;
    elements.progressBar.style.width = `${progress}%`;
    elements.batchSuccess.textContent = data.success;
    elements.batchFailed.textContent = data.failed;
    elements.batchRemaining.textContent = data.total - data.completed;

    // 记录日志（避免重复）
    if (data.completed > 0) {
        const lastSuccess = parseInt(elements.batchSuccess.dataset.last || '0');
        const lastFailed = parseInt(elements.batchFailed.dataset.last || '0');

        const isRegistrationBatch = ['registration', 'batch', 'parallel', 'pipeline'].includes(currentBatchMode);
        const successLabel = isRegistrationBatch ? '账号注册成功' : '账号处理完成';
        const failedLabel = isRegistrationBatch ? '账号注册失败' : '账号处理失败';

        if (data.success > lastSuccess) {
            addLog('success', `[成功] 第 ${data.success} 个${successLabel}`);
        }
        if (data.failed > lastFailed) {
            addLog('error', `[失败] 第 ${data.failed} 个${failedLabel}`);
        }

        elements.batchSuccess.dataset.last = data.success;
        elements.batchFailed.dataset.last = data.failed;
    }
}

// 加载最近注册的账号
async function loadRecentAccounts() {
    try {
        const data = await api.get('/accounts?page=1&page_size=10');

        if (data.accounts.length === 0) {
            elements.recentAccountsTable.innerHTML = `
                <tr>
                    <td colspan="5">
                        <div class="empty-state" style="padding: var(--spacing-md);">
                            <div class="empty-state-title">暂无已注册账号</div>
                        </div>
                    </td>
                </tr>
            `;
            return;
        }

        elements.recentAccountsTable.innerHTML = data.accounts.map(account => `
            <tr data-id="${account.id}">
                <td>${account.id}</td>
                <td>
                    <span style="display:inline-flex;align-items:center;gap:4px;">
                        <span title="${escapeHtml(account.email)}">${escapeHtml(account.email)}</span>
                        <button class="btn-copy-icon copy-email-btn" data-email="${escapeHtml(account.email)}" title="复制邮箱">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                        </button>
                    </span>
                </td>
                <td class="password-cell">
                    ${account.password
                ? `<span style="display:inline-flex;align-items:center;gap:4px;">
                            <span class="password-hidden">••••••••</span>
                            <button class="btn-copy-icon copy-pwd-btn" data-pwd="${escapeHtml(account.password)}" title="复制密码">
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                            </button>
                           </span>`
                : '-'}
                </td>
                <td>
                    ${getStatusIcon(account.status)}
                </td>
            </tr>
        `).join('');

        // 绑定复制按钮事件
        elements.recentAccountsTable.querySelectorAll('.copy-email-btn').forEach(btn => {
            btn.addEventListener('click', (e) => { e.stopPropagation(); copyToClipboard(btn.dataset.email); });
        });
        elements.recentAccountsTable.querySelectorAll('.copy-pwd-btn').forEach(btn => {
            btn.addEventListener('click', (e) => { e.stopPropagation(); copyToClipboard(btn.dataset.pwd); });
        });

    } catch (error) {
        console.error('加载账号列表失败:', error);
    }
}

// 开始账号列表轮询
function startAccountsPolling() {
    // 每30秒刷新一次账号列表
    accountsPollingInterval = setInterval(() => {
        loadRecentAccounts();
    }, 30000);
}

// 添加日志
function addLog(type, message) {
    // 日志去重：使用消息内容的 hash 作为键
    const logKey = `${type}:${message}`;
    if (displayedLogs.has(logKey)) {
        return;  // 已经显示过，跳过
    }
    displayedLogs.add(logKey);

    // 限制去重集合大小，避免内存泄漏
    if (displayedLogs.size > 1000) {
        // 清空一半的记录
        const keys = Array.from(displayedLogs);
        keys.slice(0, 500).forEach(k => displayedLogs.delete(k));
    }

    const line = document.createElement('div');
    line.className = `log-line ${type}`;

    // 添加时间戳
    const timestamp = new Date().toLocaleTimeString('zh-CN', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
    });

    line.innerHTML = `<span class="timestamp">${timestamp}</span> ${escapeHtml(message)}`;
    elements.consoleLog.appendChild(line);

    // 自动滚动到底部
    elements.consoleLog.scrollTop = elements.consoleLog.scrollHeight;

    // 限制日志行数
    const lines = elements.consoleLog.querySelectorAll('.log-line');
    if (lines.length > 500) {
        lines[0].remove();
    }
}

// 获取日志类型
function getLogType(log) {
    if (typeof log !== 'string') return 'info';

    const lowerLog = log.toLowerCase();
    if (lowerLog.includes('error') || lowerLog.includes('失败') || lowerLog.includes('错误')) {
        return 'error';
    }
    if (lowerLog.includes('warning') || lowerLog.includes('警告')) {
        return 'warning';
    }
    if (lowerLog.includes('success') || lowerLog.includes('成功') || lowerLog.includes('完成')) {
        return 'success';
    }
    return 'info';
}

// 重置按钮状态
function resetButtons() {
    elements.startBtn.disabled = false;
    elements.cancelBtn.disabled = true;
    currentTask = null;
    currentBatch = null;
    // 不要重置 isBatchMode，保持用户选择的模式
    // isBatchMode = false;
    // 重置完成标志
    taskCompleted = false;
    batchCompleted = false;
    // 重置最终状态标志
    taskFinalStatus = null;
    batchFinalStatus = null;
    // 清除活跃任务标识
    activeTaskUuid = null;
    activeBatchId = null;
    // 清除 sessionStorage 持久化状态
    sessionStorage.removeItem('activeTask');
    // 断开 WebSocket
    disconnectWebSocket();
    disconnectBatchWebSocket();
}

// HTML 转义
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}


// ============== 批量任务 WebSocket 功能 ==============

// 连接批量任务 WebSocket
function connectBatchWebSocket(batchId) {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/api/ws/batch/${batchId}`;

    try {
        batchWebSocket = new WebSocket(wsUrl);

        batchWebSocket.onopen = () => {
            console.log('批量任务 WebSocket 连接成功');
            // 停止轮询（如果有）
            stopBatchPolling();
            // 开始心跳
            startBatchWebSocketHeartbeat();
        };

        batchWebSocket.onmessage = (event) => {
            const data = JSON.parse(event.data);

            if (data.type === 'log') {
                const logType = getLogType(data.message);
                addLog(logType, data.message);
            } else if (data.type === 'status') {
            if (data.mode) {
                currentBatchMode = data.mode;
            }
                // 更新进度
                if (data.total !== undefined) {
                    updateBatchProgress({
                        total: data.total,
                        completed: data.completed || 0,
                        success: data.success || 0,
                        failed: data.failed || 0
                    });
                }

                // 检查是否完成
                if (['completed', 'failed', 'cancelled', 'cancelling'].includes(data.status)) {
                    // 保存最终状态，用于 onclose 判断
                    batchFinalStatus = data.status;
                    batchCompleted = true;

                    // 断开 WebSocket（异步操作）
                    disconnectBatchWebSocket();

                    // 任务完成后再重置按钮
                    resetButtons();

                    // 只显示一次 toast
                    if (!toastShown) {
                        toastShown = true;
                        if (data.status === 'completed') {
                            addLog('success', `[完成] 批量任务完成！成功: ${data.success}, 失败: ${data.failed}, 跳过: ${data.skipped || 0}`);
                            if (data.success > 0) {
                                toast.success(`批量注册完成，成功 ${data.success} 个`);
                                loadRecentAccounts();
                            } else {
                                toast.warning('批量注册完成，但没有成功注册任何账号');
                            }
                        } else if (data.status === 'failed') {
                            addLog('error', '[错误] 批量任务执行失败');
                            toast.error('批量任务执行失败');
                        } else if (data.status === 'cancelled' || data.status === 'cancelling') {
                            addLog('warning', '[警告] 批量任务已取消');
                        }
                    }
                }
            } else if (data.type === 'pong') {
                // 心跳响应，忽略
            }
        };

        batchWebSocket.onclose = (event) => {
            console.log('批量任务 WebSocket 连接关闭:', event.code);
            stopBatchWebSocketHeartbeat();

            // 只有在任务未完成且最终状态不是完成状态时才切换到轮询
            // 使用 batchFinalStatus 而不是 currentBatch.status，因为 currentBatch 可能已被重置
            const shouldPoll = !batchCompleted &&
                batchFinalStatus === null;  // 如果 batchFinalStatus 有值，说明任务已完成

            if (shouldPoll && currentBatch) {
                console.log('切换到轮询模式');
                startBatchPolling(currentBatch.batch_id);
            }
        };

        batchWebSocket.onerror = (error) => {
            console.error('批量任务 WebSocket 错误:', error);
            stopBatchWebSocketHeartbeat();
            startBatchPolling(batchId);
        };

    } catch (error) {
        console.error('批量任务 WebSocket 连接失败:', error);
        startBatchPolling(batchId);
    }
}

// 断开批量任务 WebSocket
function disconnectBatchWebSocket() {
    stopBatchWebSocketHeartbeat();
    if (batchWebSocket) {
        batchWebSocket.close();
        batchWebSocket = null;
    }
}

// 开始批量任务心跳
function startBatchWebSocketHeartbeat() {
    stopBatchWebSocketHeartbeat();
    batchWsHeartbeatInterval = setInterval(() => {
        if (batchWebSocket && batchWebSocket.readyState === WebSocket.OPEN) {
            batchWebSocket.send(JSON.stringify({ type: 'ping' }));
        }
    }, 25000);  // 每 25 秒发送一次心跳
}

// 停止批量任务心跳
function stopBatchWebSocketHeartbeat() {
    if (batchWsHeartbeatInterval) {
        clearInterval(batchWsHeartbeatInterval);
        batchWsHeartbeatInterval = null;
    }
}

// 发送批量任务取消请求
function cancelBatchViaWebSocket() {
    if (batchWebSocket && batchWebSocket.readyState === WebSocket.OPEN) {
        batchWebSocket.send(JSON.stringify({ type: 'cancel' }));
    }
}

// 开始轮询批量状态（降级方案）
function startBatchPolling(batchId) {
    batchPollingInterval = setInterval(async () => {
        try {
            const data = await api.get(`/registration/batch/${batchId}`);

            // 更新进度
            updateBatchProgress({
                total: data.total,
                completed: data.completed,
                success: data.success,
                failed: data.failed
            });

            // 输出日志
            if (data.logs && data.logs.length > 0) {
                const lastLogIndex = batchPollingInterval.lastLogIndex || 0;
                for (let i = lastLogIndex; i < data.logs.length; i++) {
                    const log = data.logs[i];
                    const logType = getLogType(log);
                    addLog(logType, log);
                }
                batchPollingInterval.lastLogIndex = data.logs.length;
            }

            // 检查是否完成
            if (data.finished) {
                stopBatchPolling();
                resetButtons();

                // 只显示一次 toast
                if (!toastShown) {
                    toastShown = true;
                    addLog('info', `[完成] 批量任务完成！成功: ${data.success}, 失败: ${data.failed}, 跳过: ${data.skipped || 0}`);
                    if (data.success > 0) {
                        toast.success(`批量注册完成，成功 ${data.success} 个`);
                        loadRecentAccounts();
                    } else {
                        toast.warning('批量注册完成，但没有成功注册任何账号');
                    }
                }
            }
        } catch (error) {
            console.error('轮询批量状态失败:', error);
        }
    }, 2000);

    batchPollingInterval.lastLogIndex = 0;
}

// ============== 页面可见性重连机制 ==============

function initVisibilityReconnect() {
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState !== 'visible') return;

        // 页面重新可见时，检查是否需要重连（针对同页面标签切换场景）
        const wsDisconnected = !webSocket || webSocket.readyState === WebSocket.CLOSED;
        const batchWsDisconnected = !batchWebSocket || batchWebSocket.readyState === WebSocket.CLOSED;

        // 单任务重连
        if (activeTaskUuid && !taskCompleted && wsDisconnected) {
            console.log('[重连] 页面重新可见，重连单任务 WebSocket:', activeTaskUuid);
            addLog('info', '系统: 任务已重连...');
            connectWebSocket(activeTaskUuid);
        }

        // 批量任务重连
        if (activeBatchId && !batchCompleted && batchWsDisconnected) {
            console.log('[重连] 页面重新可见，重连批量任务 WebSocket:', activeBatchId);
            addLog('info', '系统: 批量任务已重连...');
            connectBatchWebSocket(activeBatchId);
        }
    });
}

// 页面加载时恢复进行中的任务（处理跨页面导航后回到注册页的情况）
async function restoreActiveTask() {
    const saved = sessionStorage.getItem('activeTask');
    if (!saved) return;

    let state;
    try {
        state = JSON.parse(saved);
    } catch {
        sessionStorage.removeItem('activeTask');
        return;
    }

    const { mode, task_uuid, batch_id, total } = state;

    if (mode === 'single' && task_uuid) {
        // 查询任务是否仍在运行
        try {
            const data = await api.get(`/registration/tasks/${task_uuid}`);
            if (['completed', 'failed', 'cancelled'].includes(data.status)) {
                sessionStorage.removeItem('activeTask');
                return;
            }
            // 任务仍在运行，恢复状态
            currentTask = data;
            activeTaskUuid = task_uuid;
            taskCompleted = false;
            taskFinalStatus = null;
            toastShown = false;
            displayedLogs.clear();
            elements.startBtn.disabled = true;
            elements.cancelBtn.disabled = false;
            showTaskStatus(data, data.email_service || '-');
            updateTaskStatus(data.status);
            addLog('info', `系统: 检测到待续任务 (${task_uuid.substring(0, 8)})`);
            connectWebSocket(task_uuid);
        } catch {
            sessionStorage.removeItem('activeTask');
        }
    } else if (mode === 'batch' && batch_id) {
        // 查询批量任务是否仍在运行
        try {
            const data = await api.get(`/registration/batch/${batch_id}`);
            if (data.finished) {
                sessionStorage.removeItem('activeTask');
                return;
            }
            // 批量任务仍在运行，恢复状态
            currentBatch = { batch_id, ...data };
            activeBatchId = batch_id;
            currentBatchMode = mode || 'registration';
            batchCompleted = false;
            batchFinalStatus = null;
            toastShown = false;
            displayedLogs.clear();
            elements.startBtn.disabled = true;
            elements.cancelBtn.disabled = false;
            showBatchStatus({ count: total || data.total });
            updateBatchProgress(data);
            addLog('info', `系统: 检测到待续批量任务 (${batch_id.substring(0, 8)})`);
            connectBatchWebSocket(batch_id);
        } catch {
            sessionStorage.removeItem('activeTask');
        }
    }
}
// 轮询并更新活跃任务选择器
function startActiveTasksPolling() {
    setInterval(async () => {
        try {
            const data = await api.get('/registration/active-tasks');
            const selector = elements.monitorTaskSelector;
            if (!selector) return;

            const hasActive = (data.batches && data.batches.length > 0) || data.single_task;
            selector.style.display = hasActive ? 'block' : 'none';

            if (!hasActive) return;

            const currentVal = selector.value;
            let options = '<option value="">监听其他正在进行的任务...</option>';

            if (data.single_task) {
                options += `<option value="single:${data.single_task.task_uuid}">单体任务: ${data.single_task.email || data.single_task.task_uuid.substring(0, 8)}</option>`;
            }

            if (data.batches) {
                data.batches.forEach(b => {
                    options += `<option value="batch:${b.batch_id}:${b.mode}">批量任务: ${b.mode} (${b.batch_id.substring(0, 8)})</option>`;
                });
            }

            selector.innerHTML = options;
            if (currentVal && selector.querySelector(`option[value="${currentVal}"]`)) {
                selector.value = currentVal;
            }
        } catch (e) {
            console.warn('获取活跃任务列表失败', e);
        }
    }, 5000);
}
