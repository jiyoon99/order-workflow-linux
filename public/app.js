const state = { orders: [], archivedOrders: [], cancelledOrders: [], asHistoryOrders: [], user: null, setupRequired: false, authMode: "login", selectedChannel: "all", managementDrafts: {} };
const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", "'":"&#39;", '"':"&quot;" })[char]);
const formatDate = (value) => value ? new Date(value).toLocaleString("ko-KR", { dateStyle:"short", timeStyle:"short" }) : "";
const formatAmount = (value) => Number(value || 0).toLocaleString("ko-KR");
const roleLabels = { owner:"총책임자", developer:"개발자", as_manager:"AS 담당자", sales_manager:"판매 담당자", md:"MD", worker:"일반 작업자", admin:"총책임자" };
const roleOrder = ["owner", "developer", "as_manager", "sales_manager", "md", "worker"];
const memberManagementRoles = new Set(["owner", "developer"]);
const orderAdminRoles = new Set(["owner", "developer", "sales_manager", "md"]);
const asHistoryRoles = new Set(["owner", "developer", "as_manager"]);
const roleLabel = (role) => roleLabels[role] || role;
const roleOptions = (selected, allowedRoles = roleOrder) => allowedRoles
  .map((role) => `<option value="${role}" ${role === selected ? "selected" : ""}>${roleLabel(role)}</option>`).join("");

function productDetails(order) {
  const optionParts = String(order.optionName || "").split(" / ").map((value) => value.trim()).filter(Boolean);
  const registeredOption = String(order.productCode || optionParts[0] || "").trim();
  const extras = optionParts.filter((value, index) => !(index === 0 && value === registeredOption));
  return { registeredOption, extras };
}

function channelTone(channel) {
  const value = String(channel || "");
  if (value.includes("쿠팡")) return "coupang";
  if (value.includes("카카오")) return "kakao";
  if (value.includes("고도몰") || value.includes("자사")) return "godomall";
  if (value.includes("전화") || value.includes("방문") || value.includes("수기")) return "manual";
  return "other";
}

function orderStatus(order) {
  if (order.shippingDone) return { tone:"shipped", label:"출고 완료" };
  if (order.productionDone) return { tone:"produced", label:"제작 완료" };
  if (order.preparing) return { tone:"preparing", label:"준비 중" };
  return { tone:"waiting", label:"제작 대기" };
}

function orderStatusKey(order) {
  return order.shippingDone ? "shipped" : order.productionDone ? "produced" : order.preparing ? "preparing" : "waiting";
}

function worker() { return state.user?.displayName || ""; }
function canManageOrders() { return orderAdminRoles.has(state.user?.role === "admin" ? "owner" : state.user?.role); }
function canCancelOrder(order) {
  const role = state.user?.role === "admin" ? "owner" : state.user?.role;
  return orderAdminRoles.has(role) && !order.shippingDone && (!order.productionDone || memberManagementRoles.has(role));
}
function showMessage(text, error = false) { $("#message").textContent = text; $("#message").classList.toggle("error", error); }
function showManualMessage(text, error = false) { $("#manual-message").textContent = text; $("#manual-message").classList.toggle("error", error); }

function updateAccessUI() {
  const role = state.user?.role === "admin" ? "owner" : state.user?.role;
  const canManageMembers = memberManagementRoles.has(role);
  const canManageOrders = orderAdminRoles.has(role);
  const canViewAsHistory = asHistoryRoles.has(role);
  $("#members-tab").toggleAttribute("hidden", !canManageMembers);
  $("#as-history-tab").toggleAttribute("hidden", !canViewAsHistory);
  $("#upload-form").toggleAttribute("hidden", !canManageOrders);
  $("#export-button").toggleAttribute("hidden", !canManageOrders);
  $("#manual-order").toggleAttribute("hidden", !canManageOrders);
  if (!canManageMembers && !$("#members-view").hidden) showWorkspaceView("orders-view");
  if (!canViewAsHistory && !$("#as-history-view").hidden) showWorkspaceView("orders-view");
}

function showWorkspaceView(viewId) {
  document.querySelectorAll(".workspace-view").forEach((view) => { view.hidden = view.id !== viewId; });
  document.querySelectorAll(".workspace-tab").forEach((tab) => { tab.classList.toggle("is-active", tab.dataset.view === viewId); });
  if (viewId === "members-view") loadUsers();
  if (viewId === "archived-view") loadArchivedOrders();
  if (viewId === "cancelled-view") loadCancelledOrders();
  if (viewId === "as-history-view") loadAsHistory();
}

function setAuthMode(mode) {
  state.authMode = state.setupRequired ? "setup" : mode;
  const needsName = state.authMode !== "login";
  $("#display-name-field").hidden = !needsName;
  $("#display-name-field input").required = needsName;
  $("#auth-tabs").hidden = state.setupRequired;
  document.querySelectorAll("[data-auth-mode]").forEach((button) => { button.classList.toggle("is-active", button.dataset.authMode === mode); });
  $("#auth-title").textContent = state.setupRequired ? "최초 관리자 설정" : state.authMode === "register" ? "회원가입" : "로그인";
  $("#auth-help").textContent = state.setupRequired
    ? "처음 사용할 관리자 계정을 생성하세요."
    : state.authMode === "register" ? "가입한 계정은 일반 작업자 권한으로 생성됩니다." : "아이디와 비밀번호로 로그인하세요.";
  $("#auth-form button[type=submit]").textContent = state.setupRequired ? "관리자 계정 생성" : state.authMode === "register" ? "가입하기" : "로그인";
  $("#auth-message").textContent = "";
}

function filteredOrders() {
  const query = $("#search").value.trim().toLowerCase();
  const channel = state.selectedChannel;
  const status = $("#status-filter").value;
  return state.orders.filter((order) => {
    const matchesText = !query || [order.orderNumber, order.productName, order.optionName, order.recipient, order.productCode].join(" ").toLowerCase().includes(query);
    const matchesChannel = channel === "all" || order.channel === channel;
    const matchesStatus = status === "all" || orderStatusKey(order) === status;
    return matchesText && matchesChannel && matchesStatus;
  });
}

function orderTime(order) {
  const value = String(order.orderedAt || "").trim();
  const timestamp = Date.parse(value.replace(" ", "T"));
  return Number.isNaN(timestamp) ? 0 : timestamp;
}

function compareOrders(left, right) {
  const timeDifference = orderTime(left) - orderTime(right);
  if (timeDifference) return timeDifference;
  return String(left.orderNumber || "").localeCompare(String(right.orderNumber || ""), "ko", { numeric:true, sensitivity:"base" });
}

function syncStatusCards() {
  const selected = $("#status-filter").value;
  document.querySelectorAll(".stat-card").forEach((card) => {
    const active = card.dataset.status === selected;
    card.classList.toggle("is-active", active);
    card.setAttribute("aria-pressed", String(active));
  });
}

function syncChannelFilter() {
  const preferredOrder = ["고도몰", "쿠팡", "카카오"];
  const channels = [...new Set(state.orders.map((order) => order.channel).filter(Boolean))]
    .sort((left, right) => {
      const leftIndex = preferredOrder.indexOf(left);
      const rightIndex = preferredOrder.indexOf(right);
      if (leftIndex >= 0 || rightIndex >= 0) return (leftIndex < 0 ? 999 : leftIndex) - (rightIndex < 0 ? 999 : rightIndex);
      return left.localeCompare(right, "ko");
    });
  if (state.selectedChannel !== "all" && !channels.includes(state.selectedChannel)) state.selectedChannel = "all";
  $("#channel-filter").innerHTML = [
    { value:"all", label:"전체", count:state.orders.length },
    ...channels.map((channel) => ({ value:channel, label:channel, count:state.orders.filter((order) => order.channel === channel).length })),
  ].map((item) => `<button type="button" data-channel="${escapeHtml(item.value)}" class="channel-filter--${item.value === "all" ? "all" : channelTone(item.value)} ${state.selectedChannel === item.value ? "is-active" : ""}">${escapeHtml(item.label)} <strong>${item.count}</strong></button>`).join("");
}

function render() {
  syncStatusCards();
  const visible = filteredOrders().sort(compareOrders);
  $("#total-count").textContent = state.orders.length;
  $("#waiting-count").textContent = state.orders.filter((order) => orderStatusKey(order) === "waiting").length;
  $("#preparing-count").textContent = state.orders.filter((order) => orderStatusKey(order) === "preparing").length;
  $("#produced-count").textContent = state.orders.filter((order) => orderStatusKey(order) === "produced").length;
  $("#shipped-count").textContent = state.orders.filter((order) => orderStatusKey(order) === "shipped").length;
  $("#visible-count").textContent = `${visible.length}건 표시`;
  $("#empty").hidden = visible.length > 0;
  $("#orders-body").innerHTML = visible.map((order, index) => {
    const details = productDetails(order);
    const status = orderStatus(order);
    const channelClass = channelTone(order.channel);
    return `
    <tr data-id="${order.id}" class="${order.preparing ? "is-preparing" : ""} ${order.productionDone ? "is-produced" : ""}">
      <td data-label="채널 / 주문"><div class="order-sequence">${index + 1}</div><div class="order-badges"><span class="channel channel--${channelClass}">${escapeHtml(order.channel)}</span><span class="status-badge status-badge--${status.tone}">${status.label}</span></div><span class="order-number-label">주문번호</span><strong class="order-number-value">${escapeHtml(order.orderNumber)}</strong><span class="order-no">${escapeHtml(order.orderedAt)}</span></td>
      <td data-label="상품 정보"><div class="product-info"><span class="product-label">상품명 + 옵션명</span><div class="product">${escapeHtml(order.productName)}</div>${details.registeredOption ? `<div class="registered-option"><span>등록옵션명</span><strong>${escapeHtml(details.registeredOption)}</strong></div>` : ""}${details.extras.length ? `<div class="extra-options"><span>추가 상품/옵션</span>${details.extras.map((option) => `<em>${escapeHtml(option)}</em>`).join("")}</div>` : ""}<div class="product-summary"><span>수량 <strong>${order.quantity}개</strong></span><span>결제금액 <strong>${formatAmount(order.amount)}원</strong></span></div></div></td>
      <td data-label="배송지"><strong>${escapeHtml(order.recipient)}</strong> · ${escapeHtml(order.phone)}<div class="address">${escapeHtml(order.address)}</div><span class="meta">${escapeHtml(order.deliveryMessage)}</span></td>
      <td data-label="준비 중"><div class="check-card"><input class="preparing-check" type="checkbox" ${order.preparing ? "checked" : ""}><label>준비 중</label><small>${order.preparing ? `${escapeHtml(order.preparingBy)} · ${formatDate(order.preparingAt)}` : "작업자 미지정"}</small></div></td>
      <td data-label="제품 관리번호"><div class="management-field"><input class="management-number" maxlength="100" placeholder="바코드 스캔" value="${escapeHtml(state.managementDrafts[order.id] ?? order.managementNumber)}"><button class="save-management" type="button">저장</button><small>${order.managementNumberBy ? `${escapeHtml(order.managementNumberBy)} · ${formatDate(order.managementNumberAt)}` : "미등록"}</small></div></td>
      <td data-label="제작 완료"><div class="check-card"><input class="production-check" type="checkbox" ${order.productionDone ? "checked" : ""}><label>제작 완료</label><small>${order.productionDone ? `${escapeHtml(order.productionBy)} · ${formatDate(order.productionAt)}` : "담당자 미지정"}</small></div></td>
      <td data-label="출고 처리"><div class="check-card"><input class="shipping-check" type="checkbox" ${order.shippingDone ? "checked" : ""}><label>출고 완료</label><small>${order.shippingDone ? `${escapeHtml(order.shippingBy)} · ${formatDate(order.shippingAt)}` : "출고 전"}</small><div class="shipping-fields"><input class="courier" placeholder="택배사" value="${escapeHtml(order.courier)}"></div>${canCancelOrder(order) ? `<button class="cancel-order" type="button">주문 취소</button>` : ""}</div></td>
    </tr>`;
  }).join("");
}

async function loadOrders() {
  const response = await fetch("/api/orders");
  if (!response.ok) throw new Error("주문을 불러오지 못했습니다.");
  state.orders = await response.json();
  syncChannelFilter();
  render();
}

function renderArchivedOrders() {
  const query = $("#archived-search").value.trim().toLowerCase();
  const visible = state.archivedOrders
    .filter((order) => !query || [order.orderNumber, order.productName, order.optionName, order.recipient, order.channel, order.productCode].join(" ").toLowerCase().includes(query))
    .sort(compareOrders);
  $("#archived-count").textContent = `${visible.length}건`;
  $("#archived-empty").hidden = visible.length > 0;
  $("#archived-orders-body").innerHTML = visible.map((order, index) => {
    const details = productDetails(order);
    return `<tr>
      <td>${index + 1}</td>
      <td><span class="channel channel--${channelTone(order.channel)}">${escapeHtml(order.channel)}</span><strong class="order-number-value">${escapeHtml(order.orderNumber)}</strong><span class="order-no">${escapeHtml(order.orderedAt)}</span></td>
      <td><div class="product-info"><div class="product">${escapeHtml(order.productName)}</div>${details.registeredOption ? `<div class="registered-option"><span>등록옵션명</span><strong>${escapeHtml(details.registeredOption)}</strong></div>` : ""}${details.extras.length ? `<div class="extra-options">${details.extras.map((option) => `<em>${escapeHtml(option)}</em>`).join("")}</div>` : ""}<div class="product-summary"><span>수량 <strong>${order.quantity}개</strong></span><span>결제금액 <strong>${formatAmount(order.amount)}원</strong></span></div></div></td>
      <td><strong>${escapeHtml(order.recipient)}</strong> · ${escapeHtml(order.phone)}<div class="address">${escapeHtml(order.address)}</div><span class="meta">${escapeHtml(order.deliveryMessage)}</span></td>
      <td><strong>${escapeHtml(order.productionBy)}</strong><span class="meta">${formatDate(order.productionAt)}</span><span class="meta">관리번호 ${escapeHtml(order.managementNumber || "-")}</span></td>
      <td><strong>${escapeHtml(order.shippingBy)}</strong><span class="meta">${formatDate(order.shippingAt)}</span><span class="meta">${escapeHtml(order.courier || "택배사 미등록")}</span></td>
    </tr>`;
  }).join("");
}

async function loadArchivedOrders() {
  const response = await fetch("/api/orders/archived");
  if (!response.ok) throw new Error("완료 주문을 불러오지 못했습니다.");
  state.archivedOrders = await response.json();
  renderArchivedOrders();
}

function renderCancelledOrders() {
  const query = $("#cancelled-search").value.trim().toLowerCase();
  const visible = state.cancelledOrders
    .filter((order) => !query || [order.orderNumber, order.productName, order.optionName, order.recipient, order.channel, order.productCode, order.cancelReason].join(" ").toLowerCase().includes(query))
    .sort(compareOrders);
  $("#cancelled-count").textContent = `${visible.length}건`;
  $("#cancelled-empty").hidden = visible.length > 0;
  $("#cancelled-orders-body").innerHTML = visible.map((order, index) => `<tr>
    <td>${index + 1}</td>
    <td><span class="channel channel--${channelTone(order.channel)}">${escapeHtml(order.channel)}</span><strong class="order-number-value">${escapeHtml(order.orderNumber)}</strong><span class="order-no">${escapeHtml(order.orderedAt)}</span></td>
    <td><div class="product-info"><div class="product">${escapeHtml(order.productName)}</div><div class="meta">${escapeHtml(order.optionName)}</div><div class="product-summary"><span>수량 <strong>${order.quantity}개</strong></span><span>결제금액 <strong>${formatAmount(order.amount)}원</strong></span></div></div></td>
    <td><strong>${escapeHtml(order.recipient)}</strong> · ${escapeHtml(order.phone)}<div class="address">${escapeHtml(order.address)}</div></td>
    <td><strong>${escapeHtml(order.cancelledBy)}</strong><span class="meta">${formatDate(order.cancelledAt)}</span></td>
    <td>${escapeHtml(order.cancelReason)}</td>
  </tr>`).join("");
}

async function loadCancelledOrders() {
  const response = await fetch("/api/orders/cancelled");
  if (!response.ok) throw new Error("취소 주문을 불러오지 못했습니다.");
  state.cancelledOrders = await response.json();
  renderCancelledOrders();
}

function renderAsHistory() {
  const query = $("#as-history-search").value.trim().toLowerCase();
  if (!query) {
    $("#as-history-count").textContent = "고객을 검색하세요";
    $("#as-history-empty strong").textContent = "고객 이름을 검색하세요";
    $("#as-history-empty span").textContent = "해당 고객에게 출고된 모든 제품과 배송 이력을 확인할 수 있습니다.";
    $("#as-history-empty").hidden = false;
    $("#as-history-body").innerHTML = "";
    return;
  }
  const matches = state.asHistoryOrders.filter((order) => [order.recipient, order.phone].join(" ").toLowerCase().includes(query));
  const customers = new Map();
  for (const order of matches) {
    const key = `${String(order.recipient || "").trim()}|${String(order.phone || "").replace(/\D/g, "")}`;
    if (!customers.has(key)) customers.set(key, []);
    customers.get(key).push(order);
  }
  $("#as-history-count").textContent = `${customers.size}명 · 출고 ${matches.length}건`;
  $("#as-history-empty strong").textContent = "검색 결과가 없습니다";
  $("#as-history-empty span").textContent = "고객 이름이나 연락처를 다시 확인하세요.";
  $("#as-history-empty").hidden = customers.size > 0;
  $("#as-history-body").innerHTML = [...customers.values()].map((orders) => {
    orders.sort((left, right) => String(left.shippingAt || "").localeCompare(String(right.shippingAt || "")));
    const customer = orders[orders.length - 1];
    return `<article class="customer-card">
      <header class="customer-card-header"><div><h3>${escapeHtml(customer.recipient || "이름 미등록")}</h3><p>${escapeHtml(customer.phone || "연락처 미등록")} · ${escapeHtml(customer.address || "주소 미등록")}</p></div><strong>총 출고 ${orders.length}건</strong></header>
      <div class="customer-shipments">${orders.map((order) => `<div class="customer-shipment">
        <div><span class="customer-shipment-label">주문 / 출고</span><strong>${escapeHtml(order.orderNumber)}</strong><span class="meta">주문 ${escapeHtml(order.orderedAt)}</span><span class="meta">출고 ${formatDate(order.shippingAt)}</span></div>
        <div><span class="customer-shipment-label">출고 제품</span><strong class="product">${escapeHtml(order.productName)}</strong><span class="meta">${escapeHtml(order.optionName)}</span></div>
        <div><span class="customer-shipment-label">제품 식별</span><span class="management-badge">관리번호 ${escapeHtml(order.managementNumber || "미등록")}</span><span class="meta">상품코드 ${escapeHtml(order.productCode || "-")}</span></div>
        <div><span class="customer-shipment-label">배송 정보</span><strong>${escapeHtml(order.courier || "택배사 미등록")}</strong><span class="meta">송장 ${escapeHtml(order.trackingNumber || "미등록")}</span><span class="meta">담당 ${escapeHtml(order.shippingBy || "-")}</span></div>
      </div>`).join("")}</div>
    </article>`;
  }).join("");
}

async function loadAsHistory() {
  const response = await fetch("/api/orders/as-history");
  if (!response.ok) throw new Error("AS 출고 이력을 불러오지 못했습니다.");
  state.asHistoryOrders = await response.json();
  renderAsHistory();
}

async function cancelOrder(row) {
  const order = state.orders.find((item) => item.id === row.dataset.id);
  if (!order) return;
  const reason = prompt(`${order.orderNumber} 주문의 취소 사유를 입력하세요.`)?.trim();
  if (!reason) return;
  if (!confirm(`${order.orderNumber} 주문을 취소하시겠습니까?`)) return;
  const response = await fetch(`/api/orders/${order.id}`, {
    method:"PATCH",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify({ action:"cancel", reason }),
  });
  const result = await response.json();
  if (!response.ok) return showMessage(result.error || "주문을 취소하지 못했습니다.", true);
  state.orders = state.orders.filter((item) => item.id !== result.id);
  delete state.managementDrafts[result.id];
  syncChannelFilter();
  showMessage(`${result.orderNumber} 주문을 취소했습니다.`);
  render();
}

async function updateOrder(row, action, checked) {
  const body = { action, checked, worker: worker() };
  if (action === "shipping") {
    body.courier = row.querySelector(".courier").value;
  }
  if (action === "managementNumber") body.managementNumber = row.querySelector(".management-number").value;
  const response = await fetch(`/api/orders/${row.dataset.id}`, { method:"PATCH", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body) });
  const result = await response.json();
  if (!response.ok) {
    if (result.order) {
      const index = state.orders.findIndex((order) => order.id === result.order.id);
      if (index >= 0) state.orders[index] = result.order;
    }
    showMessage(result.error, true); render(); return;
  }
  const index = state.orders.findIndex((order) => order.id === result.id);
  state.orders[index] = result;
  if (action === "managementNumber") delete state.managementDrafts[result.id];
  showMessage(`${result.orderNumber} 주문이 저장되었습니다.`);
  render();
}

async function loadUsers() {
  if (!memberManagementRoles.has(state.user?.role)) return;
  const response = await fetch("/api/users");
  if (!response.ok) return;
  const users = await response.json();
  $("#user-list").innerHTML = users.map((user) => {
    const canEdit = state.user.role === "owner" || !["owner", "developer"].includes(user.role);
    const disabled = canEdit ? "" : "disabled";
    return `
    <div class="user-row" data-user-id="${user.id}">
      <label>로그인 아이디<input class="edit-username" value="${escapeHtml(user.username)}" ${disabled}></label>
      <label>이름<input class="edit-display-name" value="${escapeHtml(user.displayName)}" ${disabled}></label>
      <label>권한<select class="edit-role" ${disabled}>${roleOptions(user.role, canEdit ? (state.user.role === "developer" ? roleOrder.slice(2) : roleOrder) : [user.role])}</select></label>
      <label>새 비밀번호<input class="edit-password" type="password" minlength="8" placeholder="변경할 때만 입력" ${disabled}></label>
      <label class="enabled-field"><input class="edit-enabled" type="checkbox" ${user.enabled !== false ? "checked" : ""} ${disabled}> 사용</label>
      <button class="save-user primary" type="button" ${disabled}>저장</button>
      <button class="delete-user" type="button" ${user.id === state.user.id || !canEdit ? "disabled" : ""}>삭제</button>
    </div>`;
  }).join("");
}

async function initializeAuth() {
  const response = await fetch("/api/auth/status");
  const result = await response.json();
  state.user = result.user;
  state.setupRequired = result.setupRequired;
  if (!state.user) {
    updateAccessUI();
    $("#auth-screen").hidden = false;
    setAuthMode(state.setupRequired ? "setup" : "login");
    return;
  }
  $("#auth-screen").hidden = true;
  $("#current-user").textContent = `${state.user.displayName} (${roleLabel(state.user.role)})`;
  const createRoleSelect = $("#user-form select[name=role]");
  createRoleSelect.innerHTML = roleOptions("worker", state.user.role === "developer" ? roleOrder.slice(2) : roleOrder);
  updateAccessUI();
  showWorkspaceView("orders-view");
  await loadOrders();
}

document.querySelectorAll(".workspace-tab").forEach((button) => {
  button.addEventListener("click", () => showWorkspaceView(button.dataset.view));
});

$("#home-button").addEventListener("click", () => {
  showWorkspaceView("orders-view");
  state.selectedChannel = "all";
  $("#search").value = "";
  $("#status-filter").value = "all";
  $("#manual-order").open = false;
  syncChannelFilter();
  render();
  window.scrollTo({ top: 0, behavior: "smooth" });
});

document.querySelectorAll("[data-auth-mode]").forEach((button) => {
  button.addEventListener("click", () => setAuthMode(button.dataset.authMode));
});

$("#auth-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = Object.fromEntries(new FormData(event.target).entries());
  const path = state.setupRequired ? "/api/auth/setup" : state.authMode === "register" ? "/api/auth/register" : "/api/auth/login";
  const response = await fetch(path, { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload) });
  const result = await response.json();
  if (!response.ok) { $("#auth-message").textContent = result.error; return; }
  event.target.reset();
  if (state.authMode === "register") {
    setAuthMode("login");
    $("#auth-message").textContent = "회원가입이 완료됐습니다. 가입한 계정으로 로그인하세요.";
    return;
  }
  $("#auth-message").textContent = "";
  await initializeAuth();
});

$("#logout-button").addEventListener("click", async () => {
  await fetch("/api/auth/logout", { method:"POST" });
  state.user = null;
  state.orders = [];
  state.archivedOrders = [];
  state.cancelledOrders = [];
  state.asHistoryOrders = [];
  render();
  await initializeAuth();
});

$("#user-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = Object.fromEntries(new FormData(event.target).entries());
  const response = await fetch("/api/users", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload) });
  const result = await response.json();
  $("#user-message").textContent = response.ok ? `${result.displayName} ${roleLabel(result.role)} 계정을 추가했습니다.` : result.error;
  if (response.ok) { event.target.reset(); await loadUsers(); }
});
$("#user-list").addEventListener("click", async (event) => {
  const row = event.target.closest(".user-row");
  if (!row) return;
  if (event.target.classList.contains("save-user")) {
    const payload = {
      username: row.querySelector(".edit-username").value,
      displayName: row.querySelector(".edit-display-name").value,
      role: row.querySelector(".edit-role").value,
      password: row.querySelector(".edit-password").value,
      enabled: row.querySelector(".edit-enabled").checked,
    };
    const response = await fetch(`/api/users/${row.dataset.userId}`, { method:"PATCH", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload) });
    const result = await response.json();
    $("#user-message").textContent = response.ok ? `${result.displayName} 계정을 수정했습니다.` : result.error;
    if (response.ok) await loadUsers();
  }
  if (event.target.classList.contains("delete-user")) {
    const name = row.querySelector(".edit-display-name").value;
    if (!confirm(`${name} 계정을 삭제하시겠습니까?`)) return;
    const response = await fetch(`/api/users/${row.dataset.userId}`, { method:"DELETE" });
    const result = await response.json();
    $("#user-message").textContent = response.ok ? `${result.displayName} 계정을 삭제했습니다.` : result.error;
    if (response.ok) await loadUsers();
  }
});
$("#files").addEventListener("change", (event) => {
  const files = [...event.target.files];
  $("#import-button").disabled = files.length === 0;
  $("#selected-files").textContent = files.length ? `${files.length}개 선택: ${files.map((file) => file.name).join(", ")}` : "선택된 파일 없음";
  showMessage(files.length ? "파일 선택 완료. '주문 가져오기'를 눌러주세요." : "");
});
$("#search").addEventListener("input", render);
$("#archived-search").addEventListener("input", renderArchivedOrders);
$("#cancelled-search").addEventListener("input", renderCancelledOrders);
$("#as-history-search").addEventListener("input", renderAsHistory);
$("#channel-filter").addEventListener("click", (event) => {
  const button = event.target.closest("[data-channel]");
  if (!button) return;
  state.selectedChannel = button.dataset.channel;
  syncChannelFilter();
  render();
});
$("#status-filter").addEventListener("change", render);
document.querySelector(".stats").addEventListener("click", (event) => {
  const card = event.target.closest(".stat-card");
  if (!card) return;
  $("#status-filter").value = card.dataset.status;
  render();
});
$("#manual-order-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  const payload = Object.fromEntries(form.entries());
  payload.worker = worker();
  try {
    const response = await fetch("/api/orders/manual", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error);
    event.target.reset();
    event.target.elements.quantity.value = "1";
    event.target.elements.amount.value = "0";
    showManualMessage(`${result.orderNumber} 수기 주문을 등록했습니다.`);
    await loadOrders();
  } catch (error) { showManualMessage(error.message, true); }
});
$("#export-button").addEventListener("click", async () => {
  const button = $("#export-button");
  button.disabled = true;
  showMessage("누적 출고 완료 엑셀을 만들고 있습니다.");
  try {
    const response = await fetch("/api/export/shipped", { method:"POST" });
    if (!response.ok) {
      const result = await response.json();
      throw new Error(result.error || "엑셀을 만들지 못했습니다.");
    }
    const blob = await response.blob();
    const disposition = response.headers.get("Content-Disposition") || "";
    const today = new Date().toLocaleDateString("sv-SE");
    const filename = disposition.match(/filename="([^"]+)"/)?.[1] || `shipped-orders-all-${today}.xlsx`;
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    await loadOrders();
    showMessage("이전 출고 건과 새 출고 건을 하나의 누적 엑셀로 저장했습니다. 새 출고 건은 주문 목록에서 보관 처리했습니다.");
  } catch (error) {
    showMessage(error.message, true);
  } finally {
    button.disabled = false;
  }
});
$("#orders-body").addEventListener("change", (event) => {
  const row = event.target.closest("tr");
  if (event.target.classList.contains("preparing-check")) updateOrder(row, "preparing", event.target.checked);
  if (event.target.classList.contains("production-check")) updateOrder(row, "production", event.target.checked);
  if (event.target.classList.contains("shipping-check")) updateOrder(row, "shipping", event.target.checked);
});
$("#orders-body").addEventListener("click", (event) => {
  const cancelButton = event.target.closest(".cancel-order");
  if (cancelButton) return cancelOrder(cancelButton.closest("tr"));
  const button = event.target.closest(".save-management");
  if (button) updateOrder(button.closest("tr"), "managementNumber", true);
});
$("#orders-body").addEventListener("input", (event) => {
  if (!event.target.classList.contains("management-number")) return;
  const row = event.target.closest("tr");
  state.managementDrafts[row.dataset.id] = event.target.value;
});
$("#orders-body").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && event.target.classList.contains("management-number")) {
    event.preventDefault();
    updateOrder(event.target.closest("tr"), "managementNumber", true);
  }
});
$("#upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const files = $("#files").files;
  if (!files.length) return;
  const data = new FormData();
  for (const file of files) data.append("files", file);
  showMessage("주문을 가져오고 있습니다. 창을 닫지 마세요.");
  $("#import-button").disabled = true;
  try {
    const response = await fetch("/api/import", { method:"POST", body:data });
    const result = await response.json();
    if (!response.ok) return showMessage(result.error || "파일을 가져오지 못했습니다.", true);
    showMessage(`${result.added}건 추가, 중복 ${result.duplicates}건 제외${result.errors.length ? `\n일부 오류: ${result.errors.join(", ")}` : ""}`);
    event.target.reset();
    $("#selected-files").textContent = "선택된 파일 없음";
    await loadOrders();
  } catch (error) {
    showMessage(`서버 연결 또는 파일 처리 오류: ${error.message}`, true);
  } finally {
    $("#import-button").disabled = $("#files").files.length === 0;
  }
});

initializeAuth().catch((error) => { $("#auth-message").textContent = error.message; });
setInterval(() => { if (state.user) loadOrders().catch(() => {}); }, 10000);
