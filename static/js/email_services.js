/**
 * 邮箱服务页面 JavaScript
 */

// 状态
let customServices = []; // 合并 moe_mail + temp_mail + duck_mail + freemail + cloudflare_temp_email + imap_mail
let selectedCustom = new Set();

// DOM 元素
const elements = {
  // 统计
  customCount: document.getElementById("custom-count"),
  tempmailStatus: document.getElementById("tempmail-status"),
  totalEnabled: document.getElementById("total-enabled"),

  // 自定义域名（合并）
  customTable: document.getElementById("custom-services-table"),
  addCustomBtn: document.getElementById("add-custom-btn"),
  selectAllCustom: document.getElementById("select-all-custom"),

  // 临时邮箱
  tempmailForm: document.getElementById("tempmail-form"),
  tempmailApi: document.getElementById("tempmail-api"),
  tempmailEnabled: document.getElementById("tempmail-enabled"),
  testTempmailBtn: document.getElementById("test-tempmail-btn"),

  // 添加自定义域名模态框
  addCustomModal: document.getElementById("add-custom-modal"),
  addCustomForm: document.getElementById("add-custom-form"),
  closeCustomModal: document.getElementById("close-custom-modal"),
  cancelAddCustom: document.getElementById("cancel-add-custom"),
  customSubType: document.getElementById("custom-sub-type"),
  addCloudmailFields: document.getElementById("add-cloudmail-fields"),
  addFreemailFields: document.getElementById("add-freemail-fields"),
  addCloudflareTempEmailFields: document.getElementById(
    "add-cloudflare-temp-email-fields",
  ),

  // 编辑自定义域名模态框
  editCustomModal: document.getElementById("edit-custom-modal"),
  editCustomForm: document.getElementById("edit-custom-form"),
  closeEditCustomModal: document.getElementById("close-edit-custom-modal"),
  cancelEditCustom: document.getElementById("cancel-edit-custom"),
  editMoemailFields: document.getElementById("edit-moemail-fields"),
  editTempmailFields: document.getElementById("edit-tempmail-fields"),
  editDuckmailFields: document.getElementById("edit-duckmail-fields"),
  editFreemailFields: document.getElementById("edit-freemail-fields"),
  editCloudflareTempEmailFields: document.getElementById(
    "edit-cloudflare-temp-email-fields",
  ),
  editCloudmailFields: document.getElementById("edit-cloudmail-fields"),
  editImapFields: document.getElementById("edit-imap-fields"),
  editCustomTypeBadge: document.getElementById("edit-custom-type-badge"),
  editCustomSubTypeHidden: document.getElementById(
    "edit-custom-sub-type-hidden",
  ),
};

const CUSTOM_SUBTYPE_LABELS = {
  freemail: "📬 Freemail（Cloudflare Workers 临时邮箱）",
  cloudflare_temp_email: "☁️ Cloudflare Temp Email（邮箱 JWT 模式）",
  cloudmail: "☁️ CloudMail（Cloudflare Workers 邮箱）",
  imap_mail: "IMAP Catch-all（Cloudflare Email Routing + IMAP）",
};

const ADD_SUBTYPE_FIELDS_HTML = {
  freemail: `
        <div id="add-freemail-fields">
            <div class="form-group">
                <label for="add-fm-base-url">服务地址</label>
                <input type="text" id="add-fm-base-url" name="fm_base_url" placeholder="https://your-freemail.example.com">
            </div>
            <div class="form-group">
                <label for="add-fm-admin-token">管理员 Token</label>
                <input type="password" id="add-fm-admin-token" name="fm_admin_token" placeholder="请输入 JWT_TOKEN">
            </div>
            <div class="form-group">
                <label for="add-fm-domain">域名</label>
                <input type="text" id="add-fm-domain" name="fm_domain" placeholder="example.com">
            </div>
        </div>
    `,
  cloudflare_temp_email: `
        <div id="add-cloudflare-temp-email-fields">
            <div class="form-group">
                <label for="add-cfte-base-url">服务地址</label>
                <input type="text" id="add-cfte-base-url" name="cfte_base_url" placeholder="https://mail.example.com">
            </div>
            <div class="form-group">
                <label for="add-cfte-domain">域名</label>
                <input type="text" id="add-cfte-domain" name="cfte_domain" placeholder="example.com">
            </div>
            <div class="form-row">
                <div class="form-group">
                    <label for="add-cfte-timeout">超时时间</label>
                    <input type="number" id="add-cfte-timeout" name="cfte_timeout" min="1" value="120">
                </div>
                <div class="form-group">
                    <label for="add-cfte-poll-interval">轮询间隔</label>
                    <input type="number" id="add-cfte-poll-interval" name="cfte_poll_interval" min="0" value="3">
                </div>
            </div>
            <div class="form-group">
                <label for="add-cfte-mail-limit">邮件数量</label>
                <input type="number" id="add-cfte-mail-limit" name="cfte_mail_limit" min="1" value="10">
            </div>
        </div>
    `,
  cloudmail: `
        <div id="add-cloudmail-fields">
            <div class="form-group">
                <label for="add-cm-base-url">服务地址</label>
                <input type="text" id="add-cm-base-url" name="cm_base_url" placeholder="https://your-cloudmail.example.com">
            </div>
            <div class="form-group">
                <label for="add-cm-admin-email">管理员邮箱</label>
                <input type="email" id="add-cm-admin-email" name="cm_admin_email" placeholder="admin@example.com">
            </div>
            <div class="form-group">
                <label for="add-cm-admin-password">管理员密码</label>
                <input type="password" id="add-cm-admin-password" name="cm_admin_password" placeholder="请输入管理员密码">
            </div>
            <div class="form-group">
                <label for="add-cm-domain">域名</label>
                <input type="text" id="add-cm-domain" name="cm_domain" placeholder="example.com 或 a.com,b.com">
            </div>
        </div>
    `,
  imap_mail: `
        <div id="add-imap-fields">
            <div class="form-group">
                <label for="add-imap-domain">收信域名</label>
                <input type="text" id="add-imap-domain" name="imap_domain" placeholder="example.com">
            </div>
            <div class="form-group">
                <label for="add-imap-host">IMAP 主机</label>
                <input type="text" id="add-imap-host" name="imap_host" placeholder="imap.example.com">
            </div>
            <div class="form-row">
                <div class="form-group">
                    <label for="add-imap-port">端口</label>
                    <input type="number" id="add-imap-port" name="imap_port" value="993">
                </div>
                <div class="form-group">
                    <label for="add-imap-use-ssl">SSL</label>
                    <select id="add-imap-use-ssl" name="imap_use_ssl">
                        <option value="true">启用</option>
                    </select>
                </div>
            </div>
            <div class="form-group">
                <label for="add-imap-email">登录邮箱</label>
                <input type="email" id="add-imap-email" name="imap_email" placeholder="username@example.com">
            </div>
            <div class="form-group">
                <label for="add-imap-password">密码 / App Password</label>
                <input type="password" id="add-imap-password" name="imap_password">
            </div>
            <div class="form-row">
                <div class="form-group">
                    <label for="add-imap-timeout">超时时间</label>
                    <input type="number" id="add-imap-timeout" name="imap_timeout" min="1" value="120">
                </div>
                <div class="form-group">
                    <label for="add-imap-poll-interval">轮询间隔</label>
                    <input type="number" id="add-imap-poll-interval" name="imap_poll_interval" min="0" value="5">
                </div>
            </div>
        </div>
    `,
};

function bindIfPresent(element, eventName, handler) {
  if (element) element.addEventListener(eventName, handler);
}

function ensureAddCustomFieldsRendered() {
  const container = document.getElementById("add-fields-container");
  if (!container || container.children.length > 0) return;
  container.innerHTML = Object.values(ADD_SUBTYPE_FIELDS_HTML).join("");
  elements.addFreemailFields = document.getElementById("add-freemail-fields");
  elements.addCloudflareTempEmailFields = document.getElementById(
    "add-cloudflare-temp-email-fields",
  );
  elements.addCloudmailFields = document.getElementById("add-cloudmail-fields");
  elements.addImapFields = document.getElementById("add-imap-fields");
}

// 初始化
document.addEventListener("DOMContentLoaded", () => {
  ensureAddCustomFieldsRendered();
  loadStats();
  loadCustomServices();
  loadTempmailConfig();
  initEventListeners();
});

// 事件监听
function initEventListeners() {
  // 自定义域名全选
  bindIfPresent(elements.selectAllCustom, "change", (e) => {
    const checkboxes = elements.customTable.querySelectorAll(
      'input[type="checkbox"][data-id]',
    );
    checkboxes.forEach((cb) => {
      cb.checked = e.target.checked;
      const id = parseInt(cb.dataset.id);
      if (e.target.checked) selectedCustom.add(id);
      else selectedCustom.delete(id);
    });
  });

  // 添加自定义域名
  bindIfPresent(elements.addCustomBtn, "click", () => {
    elements.addCustomForm.reset();
    ensureAddCustomFieldsRendered();
    switchAddSubType("freemail");
    elements.addCustomModal.classList.add("active");
  });
  bindIfPresent(elements.closeCustomModal, "click", () =>
    elements.addCustomModal.classList.remove("active"),
  );
  bindIfPresent(elements.cancelAddCustom, "click", () =>
    elements.addCustomModal.classList.remove("active"),
  );
  bindIfPresent(elements.addCustomForm, "submit", handleAddCustom);

  // 类型切换（添加表单）
  bindIfPresent(elements.customSubType, "change", (e) =>
    switchAddSubType(e.target.value),
  );

  // 编辑自定义域名
  bindIfPresent(elements.closeEditCustomModal, "click", () =>
    elements.editCustomModal.classList.remove("active"),
  );
  bindIfPresent(elements.cancelEditCustom, "click", () =>
    elements.editCustomModal.classList.remove("active"),
  );
  bindIfPresent(elements.editCustomForm, "submit", handleEditCustom);

  // 临时邮箱配置
  bindIfPresent(elements.tempmailForm, "submit", handleSaveTempmail);
  bindIfPresent(elements.testTempmailBtn, "click", handleTestTempmail);

  // 点击其他地方关闭更多菜单
  document.addEventListener("click", () => {
    document
      .querySelectorAll(".dropdown-menu.active")
      .forEach((m) => m.classList.remove("active"));
  });
}

function toggleEmailMoreMenu(btn) {
  const menu = btn.nextElementSibling;
  const isActive = menu.classList.contains("active");
  document
    .querySelectorAll(".dropdown-menu.active")
    .forEach((m) => m.classList.remove("active"));
  if (!isActive) menu.classList.add("active");
}

function closeEmailMoreMenu(el) {
  const menu = el.closest(".dropdown-menu");
  if (menu) menu.classList.remove("active");
}

// 切换添加表单子类型
function switchAddSubType(subType) {
  ensureAddCustomFieldsRendered();
  if (elements.customSubType) elements.customSubType.value = subType;
  if (elements.addFreemailFields)
    elements.addFreemailFields.style.display =
      subType === "freemail" ? "" : "none";
  if (elements.addCloudflareTempEmailFields)
    elements.addCloudflareTempEmailFields.style.display =
      subType === "cloudflare_temp_email" ? "" : "none";
  if (elements.addCloudmailFields)
    elements.addCloudmailFields.style.display =
      subType === "cloudmail" ? "" : "none";
  if (elements.addImapFields)
    elements.addImapFields.style.display =
      subType === "imap_mail" ? "" : "none";
}

// 切换编辑表单子类型显示
function switchEditSubType(subType) {
  elements.editCustomSubTypeHidden.value = subType;
  elements.editMoemailFields.style.display =
    subType === "moemail" ? "" : "none";
  elements.editTempmailFields.style.display =
    subType === "tempmail" ? "" : "none";
  elements.editDuckmailFields.style.display =
    subType === "duckmail" ? "" : "none";
  elements.editFreemailFields.style.display =
    subType === "freemail" ? "" : "none";
  elements.editCloudflareTempEmailFields.style.display =
    subType === "cloudflare_temp_email" ? "" : "none";
  elements.editCloudmailFields.style.display =
    subType === "cloudmail" ? "" : "none";
  elements.editImapFields.style.display = subType === "imap_mail" ? "" : "none";
  elements.editCustomTypeBadge.textContent =
    CUSTOM_SUBTYPE_LABELS[subType] || CUSTOM_SUBTYPE_LABELS.moemail;
}

// 加载统计信息
async function loadStats() {
  try {
    const data = await api.get("/email-services/stats");
    elements.customCount.textContent =
      (data.custom_count || 0) +
      (data.temp_mail_count || 0) +
      (data.duck_mail_count || 0) +
      (data.freemail_count || 0) +
      (data.cloudflare_temp_email_count || 0) +
      (data.imap_mail_count || 0);
    elements.tempmailStatus.textContent = data.tempmail_available
      ? "可用"
      : "不可用";
    elements.totalEnabled.textContent = data.enabled_count || 0;
  } catch (error) {
    console.error("加载统计信息失败:", error);
  }
}

function getCustomServiceTypeBadge(subType) {
  if (subType === "moemail") {
    return '<span class="status-badge info">MoeMail</span>';
  }
  if (subType === "tempmail") {
    return '<span class="status-badge warning">TempMail</span>';
  }
  if (subType === "duckmail") {
    return '<span class="status-badge success">DuckMail</span>';
  }
  if (subType === "freemail") {
    return '<span class="status-badge" style="background-color:#9c27b0;color:white;">Freemail</span>';
  }
  if (subType === "cloudflare_temp_email") {
    return '<span class="status-badge" style="background-color:#455a64;color:white;">CF Temp</span>';
  }
  if (subType === "cloudmail") {
    return '<span class="status-badge" style="background-color:#00bcd4;color:white;">CloudMail</span>';
  }
  return '<span class="status-badge" style="background-color:#0288d1;color:white;">IMAP</span>';
}

function getCustomServiceAddress(service) {
  if (service._subType === "imap_mail") {
    const host = service.config?.host || "-";
    const emailAddr = service.config?.email || "";
    const domain = service.config?.domain || "";
    return `${escapeHtml(host)}<div style="color: var(--text-muted); margin-top: 4px;">${escapeHtml(emailAddr)}${domain ? " / " + escapeHtml(domain) : ""}</div>`;
  }
  const baseUrl = service.config?.base_url || "-";
  const domain = service.config?.default_domain || service.config?.domain;
  if (!domain) {
    return escapeHtml(baseUrl);
  }
  return `${escapeHtml(baseUrl)}<div style="color: var(--text-muted); margin-top: 4px;">默认域名：@${escapeHtml(domain)}</div>`;
}

// 加载自定义邮箱服务（moe_mail + temp_mail + duck_mail + freemail 合并）
async function loadCustomServices() {
  try {
    const [r1, r2, r3, r4, r5, r6, r7] = await Promise.all([
      api.get("/email-services?service_type=moe_mail"),
      api.get("/email-services?service_type=temp_mail"),
      api.get("/email-services?service_type=duck_mail"),
      api.get("/email-services?service_type=freemail"),
      api.get("/email-services?service_type=imap_mail"),
      api.get("/email-services?service_type=cloud_mail"),
      api.get("/email-services?service_type=cloudflare_temp_email"),
    ]);
    customServices = [
      ...(r1.services || []).map((s) => ({ ...s, _subType: "moemail" })),
      ...(r2.services || []).map((s) => ({ ...s, _subType: "tempmail" })),
      ...(r3.services || []).map((s) => ({ ...s, _subType: "duckmail" })),
      ...(r4.services || []).map((s) => ({ ...s, _subType: "freemail" })),
      ...(r5.services || []).map((s) => ({ ...s, _subType: "imap_mail" })),
      ...(r6.services || []).map((s) => ({ ...s, _subType: "cloudmail" })),
      ...(r7.services || []).map((s) => ({
        ...s,
        _subType: "cloudflare_temp_email",
      })),
    ];

    if (customServices.length === 0) {
      elements.customTable.innerHTML = `
                <tr>
                    <td colspan="8">
                        <div class="empty-state">
                            <div class="empty-state-title">暂无自定义邮箱服务</div>
                            <div class="empty-state-description">点击「添加服务」按钮创建新服务</div>
                        </div>
                    </td>
                </tr>
            `;
      return;
    }

    elements.customTable.innerHTML = customServices
      .map((service) => {
        return `
            <tr data-id="${service.id}">
                <td><input type="checkbox" data-id="${service.id}" ${selectedCustom.has(service.id) ? "checked" : ""}></td>
                <td>${escapeHtml(service.name)}</td>
                <td>${getCustomServiceTypeBadge(service._subType)}</td>
                <td style="font-size: 0.75rem;">${getCustomServiceAddress(service)}</td>
                <td title="${service.enabled ? "已启用" : "已禁用"}">${service.enabled ? "正常" : "禁用"}</td>
                <td>${service.priority}</td>
                <td>${format.date(service.last_used)}</td>
                <td>
                    <div style="display:flex;gap:4px;align-items:center;white-space:nowrap;">
                        <button class="btn btn-secondary btn-sm" onclick="editCustomService(${service.id}, '${service._subType}')">编辑</button>
                        <div class="dropdown" style="position:relative;">
                            <button class="btn btn-secondary btn-sm" onclick="event.stopPropagation();toggleEmailMoreMenu(this)">更多</button>
                            <div class="dropdown-menu" style="min-width:80px;">
                                <a href="#" class="dropdown-item" onclick="event.preventDefault();closeEmailMoreMenu(this);toggleService(${service.id}, ${!service.enabled})">${service.enabled ? "禁用" : "启用"}</a>
                                <a href="#" class="dropdown-item" onclick="event.preventDefault();closeEmailMoreMenu(this);testService(${service.id})">测试</a>
                            </div>
                        </div>
                        <button class="btn btn-danger btn-sm" onclick="deleteService(${service.id}, '${escapeHtml(service.name)}')">删除</button>
                    </div>
                </td>
            </tr>`;
      })
      .join("");

    elements.customTable
      .querySelectorAll('input[type="checkbox"][data-id]')
      .forEach((cb) => {
        cb.addEventListener("change", (e) => {
          const id = parseInt(e.target.dataset.id);
          if (e.target.checked) selectedCustom.add(id);
          else selectedCustom.delete(id);
        });
      });
  } catch (error) {
    console.error("加载自定义邮箱服务失败:", error);
  }
}

// 加载临时邮箱配置
async function loadTempmailConfig() {
  try {
    const settings = await api.get("/settings");
    if (settings.tempmail) {
      elements.tempmailApi.value = settings.tempmail.api_url || "";
      elements.tempmailEnabled.checked = settings.tempmail.enabled !== false;
    }
  } catch (error) {
    // 忽略错误
  }
}

// 添加自定义邮箱服务（根据子类型区分）
async function handleAddCustom(e) {
  e.preventDefault();
  const formData = new FormData(e.target);
  const subType = formData.get("sub_type") || "freemail";
  let serviceType = "";
  let config = {};

  if (subType === "freemail") {
    serviceType = "freemail";
    config = {
      base_url: formData.get("fm_base_url"),
      admin_token: formData.get("fm_admin_token"),
      domain: formData.get("fm_domain"),
    };
  } else if (subType === "cloudflare_temp_email") {
    serviceType = "cloudflare_temp_email";
    config = {
      base_url: formData.get("cfte_base_url"),
      domain: formData.get("cfte_domain"),
      timeout: parseInt(formData.get("cfte_timeout"), 10) || 120,
      poll_interval: parseInt(formData.get("cfte_poll_interval"), 10) || 3,
      mail_limit: parseInt(formData.get("cfte_mail_limit"), 10) || 10,
    };
  } else if (subType === "imap_mail") {
    serviceType = "imap_mail";
    config = {
      domain: formData.get("imap_domain"),
      host: formData.get("imap_host"),
      port: parseInt(formData.get("imap_port"), 10) || 993,
      use_ssl: formData.get("imap_use_ssl") !== "false",
      email: formData.get("imap_email"),
      password: formData.get("imap_password"),
      timeout: parseInt(formData.get("imap_timeout"), 10) || 120,
      poll_interval: parseInt(formData.get("imap_poll_interval"), 10) || 5,
    };
  } else {
    serviceType = "cloud_mail";
    const domainInput = formData.get("cm_domain");
    let domain = domainInput;
    if (domainInput && domainInput.includes(",")) {
      domain = domainInput
        .split(",")
        .map((d) => d.trim())
        .filter((d) => d);
    }
    config = {
      base_url: formData.get("cm_base_url"),
      admin_email: formData.get("cm_admin_email"),
      admin_password: formData.get("cm_admin_password"),
      domain: domain,
    };
  }

  const data = {
    service_type: serviceType,
    name: formData.get("name"),
    config,
    enabled: formData.get("enabled") === "on",
    priority: parseInt(formData.get("priority")) || 0,
  };

  try {
    await api.post("/email-services", data);
    toast.success("服务添加成功");
    elements.addCustomModal.classList.remove("active");
    e.target.reset();
    loadCustomServices();
    loadStats();
  } catch (error) {
    toast.error("添加失败: " + error.message);
  }
}

// 切换服务状态
async function toggleService(id, enabled) {
  try {
    await api.patch(`/email-services/${id}`, { enabled });
    toast.success(enabled ? "已启用" : "已禁用");
    loadCustomServices();
    loadStats();
  } catch (error) {
    toast.error("操作失败: " + error.message);
  }
}

// 测试服务
async function testService(id) {
  try {
    const result = await api.post(`/email-services/${id}/test`);
    if (result.success) toast.success("测试成功");
    else toast.error("测试失败: " + (result.error || "未知错误"));
  } catch (error) {
    toast.error("测试失败: " + error.message);
  }
}

// 删除服务
async function deleteService(id, name) {
  const confirmed = await confirm(`确定要删除 "${name}" 吗？`);
  if (!confirmed) return;
  try {
    await api.delete(`/email-services/${id}`);
    toast.success("已删除");
    selectedCustom.delete(id);
    loadCustomServices();
    loadStats();
  } catch (error) {
    toast.error("删除失败: " + error.message);
  }
}

// 保存临时邮箱配置
async function handleSaveTempmail(e) {
  e.preventDefault();
  try {
    await api.post("/settings/tempmail", {
      api_url: elements.tempmailApi.value,
      enabled: elements.tempmailEnabled.checked,
    });
    toast.success("配置已保存");
  } catch (error) {
    toast.error("保存失败: " + error.message);
  }
}

// 测试临时邮箱
async function handleTestTempmail() {
  elements.testTempmailBtn.disabled = true;
  elements.testTempmailBtn.textContent = "测试中...";
  try {
    const result = await api.post("/email-services/test-tempmail", {
      api_url: elements.tempmailApi.value,
    });
    if (result.success) toast.success("临时邮箱连接正常");
    else toast.error("连接失败: " + (result.error || "未知错误"));
  } catch (error) {
    toast.error("测试失败: " + error.message);
  } finally {
    elements.testTempmailBtn.disabled = false;
    elements.testTempmailBtn.textContent = "🔌 测试连接";
  }
}

// HTML 转义
function escapeHtml(text) {
  if (!text) return "";
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

// ============== 编辑功能 ==============

// 编辑自定义邮箱服务（支持 moemail / tempmail / duckmail）
async function editCustomService(id, subType) {
  try {
    const service = await api.get(`/email-services/${id}/full`);
    const resolvedSubType =
      subType ||
      (service.service_type === "temp_mail"
        ? "tempmail"
        : service.service_type === "duck_mail"
          ? "duckmail"
          : service.service_type === "freemail"
            ? "freemail"
            : service.service_type === "cloudflare_temp_email"
              ? "cloudflare_temp_email"
              : service.service_type === "cloud_mail"
                ? "cloudmail"
                : service.service_type === "imap_mail"
                  ? "imap_mail"
                  : "moemail");

    document.getElementById("edit-custom-id").value = service.id;
    document.getElementById("edit-custom-name").value = service.name || "";
    document.getElementById("edit-custom-priority").value =
      service.priority || 0;
    document.getElementById("edit-custom-enabled").checked = service.enabled;

    switchEditSubType(resolvedSubType);

    if (resolvedSubType === "moemail") {
      document.getElementById("edit-custom-api-url").value =
        service.config?.base_url || "";
      document.getElementById("edit-custom-api-key").value = "";
      document.getElementById("edit-custom-api-key").placeholder = service
        .config?.api_key
        ? "已设置，留空保持不变"
        : "API Key";
      document.getElementById("edit-custom-domain").value =
        service.config?.default_domain || service.config?.domain || "";
    } else if (resolvedSubType === "tempmail") {
      document.getElementById("edit-tm-base-url").value =
        service.config?.base_url || "";
      document.getElementById("edit-tm-admin-password").value = "";
      document.getElementById("edit-tm-admin-password").placeholder = service
        .config?.admin_password
        ? "已设置，留空保持不变"
        : "请输入 Admin 密码";
      document.getElementById("edit-tm-domain").value =
        service.config?.domain || "";
    } else if (resolvedSubType === "duckmail") {
      document.getElementById("edit-dm-base-url").value =
        service.config?.base_url || "";
      document.getElementById("edit-dm-api-key").value = "";
      document.getElementById("edit-dm-api-key").placeholder = service.config
        ?.api_key
        ? "已设置，留空保持不变"
        : "请输入 API Key（可选）";
      document.getElementById("edit-dm-domain").value =
        service.config?.default_domain || "";
      document.getElementById("edit-dm-password-length").value =
        service.config?.password_length || 12;
    } else if (resolvedSubType === "freemail") {
      document.getElementById("edit-fm-base-url").value =
        service.config?.base_url || "";
      document.getElementById("edit-fm-admin-token").value = "";
      document.getElementById("edit-fm-admin-token").placeholder = service
        .config?.admin_token
        ? "已设置，留空保持不变"
        : "请输入 Admin Token";
      document.getElementById("edit-fm-domain").value =
        service.config?.domain || "";
    } else if (resolvedSubType === "cloudflare_temp_email") {
      document.getElementById("edit-cfte-base-url").value =
        service.config?.base_url || "";
      document.getElementById("edit-cfte-domain").value =
        service.config?.domain || "";
      document.getElementById("edit-cfte-timeout").value =
        service.config?.timeout || 120;
      document.getElementById("edit-cfte-poll-interval").value =
        service.config?.poll_interval || 3;
      document.getElementById("edit-cfte-mail-limit").value =
        service.config?.mail_limit || 10;
    } else if (resolvedSubType === "cloudmail") {
      document.getElementById("edit-cm-base-url").value =
        service.config?.base_url || "";
      document.getElementById("edit-cm-admin-email").value =
        service.config?.admin_email || "";
      document.getElementById("edit-cm-admin-password").value = "";
      document.getElementById("edit-cm-admin-password").placeholder = service
        .config?.admin_password
        ? "已设置，留空保持不变"
        : "请输入管理员密码";
      // 处理域名：如果是数组，转换为逗号分隔的字符串
      const domain = service.config?.domain;
      const domainStr = Array.isArray(domain)
        ? domain.join(", ")
        : domain || "";
      document.getElementById("edit-cm-domain").value = domainStr;
    } else if (resolvedSubType === "imap_mail") {
      document.getElementById("edit-imap-domain").value =
        service.config?.domain || "";
      document.getElementById("edit-imap-host").value =
        service.config?.host || "";
      document.getElementById("edit-imap-port").value =
        service.config?.port || 993;
      document.getElementById("edit-imap-use-ssl").value =
        service.config?.use_ssl !== false ? "true" : "false";
      document.getElementById("edit-imap-email").value =
        service.config?.email || "";
      document.getElementById("edit-imap-password").value = "";
      document.getElementById("edit-imap-password").placeholder = service.config
        ?.password
        ? "已设置，留空保持不变"
        : "请输入密码/授权码";
      document.getElementById("edit-imap-timeout").value =
        service.config?.timeout || 120;
      document.getElementById("edit-imap-poll-interval").value =
        service.config?.poll_interval || 5;
    }

    elements.editCustomModal.classList.add("active");
  } catch (error) {
    toast.error("获取服务信息失败: " + error.message);
  }
}

// 保存编辑自定义邮箱服务
async function handleEditCustom(e) {
  e.preventDefault();
  const id = document.getElementById("edit-custom-id").value;
  const formData = new FormData(e.target);
  const subType = formData.get("sub_type");

  let config;
  if (subType === "moemail") {
    config = {
      base_url: formData.get("api_url"),
      default_domain: formData.get("domain"),
    };
    const apiKey = formData.get("api_key");
    if (apiKey && apiKey.trim()) config.api_key = apiKey.trim();
  } else if (subType === "tempmail") {
    config = {
      base_url: formData.get("tm_base_url"),
      domain: formData.get("tm_domain"),
      enable_prefix: true,
    };
    const pwd = formData.get("tm_admin_password");
    if (pwd && pwd.trim()) config.admin_password = pwd.trim();
  } else if (subType === "duckmail") {
    config = {
      base_url: formData.get("dm_base_url"),
      default_domain: formData.get("dm_domain"),
      password_length: parseInt(formData.get("dm_password_length"), 10) || 12,
    };
    const apiKey = formData.get("dm_api_key");
    if (apiKey && apiKey.trim()) config.api_key = apiKey.trim();
  } else if (subType === "freemail") {
    config = {
      base_url: formData.get("fm_base_url"),
      domain: formData.get("fm_domain"),
    };
    const token = formData.get("fm_admin_token");
    if (token && token.trim()) config.admin_token = token.trim();
  } else if (subType === "cloudflare_temp_email") {
    config = {
      base_url: formData.get("cfte_base_url"),
      domain: formData.get("cfte_domain"),
      timeout: parseInt(formData.get("cfte_timeout"), 10) || 120,
      poll_interval: parseInt(formData.get("cfte_poll_interval"), 10) || 3,
      mail_limit: parseInt(formData.get("cfte_mail_limit"), 10) || 10,
    };
  } else if (subType === "cloudmail") {
    const domainInput = formData.get("cm_domain");
    // 处理域名：如果包含逗号，转换为数组；否则保持字符串
    let domain = domainInput;
    if (domainInput && domainInput.includes(",")) {
      domain = domainInput
        .split(",")
        .map((d) => d.trim())
        .filter((d) => d);
    }
    config = {
      base_url: formData.get("cm_base_url"),
      admin_email: formData.get("cm_admin_email"),
      domain: domain,
    };
    const pwd = formData.get("cm_admin_password");
    if (pwd && pwd.trim()) config.admin_password = pwd.trim();
  } else if (subType === "imap_mail") {
    config = {
      domain: formData.get("imap_domain"),
      host: formData.get("imap_host"),
      port: parseInt(formData.get("imap_port"), 10) || 993,
      use_ssl: formData.get("imap_use_ssl") !== "false",
      email: formData.get("imap_email"),
      timeout: parseInt(formData.get("imap_timeout"), 10) || 120,
      poll_interval: parseInt(formData.get("imap_poll_interval"), 10) || 5,
    };
    const pwd = formData.get("imap_password");
    if (pwd && pwd.trim()) config.password = pwd.trim();
  }

  const updateData = {
    name: formData.get("name"),
    priority: parseInt(formData.get("priority")) || 0,
    enabled: formData.get("enabled") === "on",
    config,
  };

  try {
    await api.patch(`/email-services/${id}`, updateData);
    toast.success("服务更新成功");
    elements.editCustomModal.classList.remove("active");
    loadCustomServices();
    loadStats();
  } catch (error) {
    toast.error("更新失败: " + error.message);
  }
}
