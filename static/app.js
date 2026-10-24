const $ = (selector, root = document) => root.querySelector(selector);
const content = $("#content");
const state = { view: "overview", from: "2026-10-01", to: "2026-10-14", report: null };
const titles = {
  overview: ["Сводка", "Всё о вашем магазине", "Главные цифры и понятная экономика продаж."],
  products: ["Товары", "Товары и их прибыль", "Что продаётся, сколько осталось и что приносит доход."],
  expenses: ["Расходы", "Каждый расход на виду", "Реклама, упаковка и другие затраты вашего магазина."]
};
const moneyFormatter = new Intl.NumberFormat("ru-RU", { style: "currency", currency: "RUB", maximumFractionDigits: 2 });
const integerFormatter = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 });
const percentFormatter = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 });
const currency = value => moneyFormatter.format(Number(value) || 0);
const integer = value => integerFormatter.format(Number(value) || 0);
const percent = value => `${percentFormatter.format(Number(value) || 0)} %`;
const plural = (value, one, few, many) => {
  const count = Math.abs(Number(value)) % 100;
  if (count >= 11 && count <= 14) return many;
  const last = count % 10;
  return last === 1 ? one : last >= 2 && last <= 4 ? few : many;
};
const dateText = value => new Date(`${value}T12:00:00`).toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" });
const shortDate = value => new Date(`${value}T12:00:00`).toLocaleDateString("ru-RU", { day: "2-digit", month: "short" });
const periodText = () => {
  const fromYear = state.from.slice(0, 4), toYear = state.to.slice(0, 4);
  return `${shortDate(state.from)}${fromYear !== toYear ? ` ${fromYear}` : ""} — ${shortDate(state.to)} ${toYear}`;
};
let productController;
let productDetailRequest = 0;
let searchTimer;
let toastTimer;

function node(tag, className = "", text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function icon(name) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  element.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`);
  element.append(use);
  return element;
}

function button(text, className, action, iconName) {
  const element = node("button", `button ${className}`);
  element.type = "button";
  if (iconName) element.append(icon(iconName));
  element.append(document.createTextNode(text));
  if (action) element.addEventListener("click", action);
  return element;
}

function svgNode(tag, attributes = {}, text) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", tag);
  Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, value));
  if (text !== undefined) element.textContent = text;
  return element;
}

function validationMessage(detail) {
  if (typeof detail === "string") return detail;
  if (!Array.isArray(detail)) return "Не удалось выполнить запрос. Попробуйте ещё раз.";
  const names = { date: "Дата", category: "Категория", amount: "Сумма", comment: "Комментарий", date_from: "Начало периода", date_to: "Конец периода" };
  const messages = { greater_than: "значение должно быть больше нуля", less_than_equal: "слишком большое значение", string_too_long: "слишком длинный текст", missing: "обязательное поле", date_from_datetime_parsing: "укажите корректную дату", literal_error: "выберите категорию из списка", decimal_max_places: "не больше двух цифр после запятой" };
  return detail.map(item => `${names[item.loc?.at(-1)] || "Данные"}: ${messages[item.type] || item.msg || "проверьте значение"}.`).join(" ");
}

async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(path, { ...options, headers: { "Accept": "application/json", ...(options.body ? { "Content-Type": "application/json" } : {}), ...options.headers } });
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new Error("Сервис недоступен. Проверьте, запущено ли приложение, и повторите попытку.");
  }
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(validationMessage(data.detail));
  }
  return response.status === 204 ? null : response.json();
}

function reportPath() {
  return `/api/report?${new URLSearchParams({ date_from: state.from, date_to: state.to })}`;
}

function loading(target, message = "Загружаем данные…") {
  const box = node("div", "loading-state");
  box.append(node("span", "spinner"), node("p", "", message));
  box.firstChild.setAttribute("aria-hidden", "true");
  target.setAttribute("aria-busy", "true");
  target.replaceChildren(box);
}

function empty(target, title, description, action) {
  const box = node("div", "empty-state");
  const symbol = node("span", "empty-symbol");
  symbol.append(icon("box"));
  box.append(symbol, node("h3", "", title), node("p", "", description));
  if (action) box.append(action);
  target.replaceChildren(box);
  target.setAttribute("aria-busy", "false");
}

function failure(target, error, retry) {
  const box = node("div", "error-state");
  box.setAttribute("role", "alert");
  box.append(node("h3", "", "Не получилось загрузить данные"), node("p", "", error.message));
  if (retry) box.append(button("Попробовать ещё раз", "button-secondary", retry));
  target.replaceChildren(box);
  target.setAttribute("aria-busy", "false");
}

function toast(message) {
  const element = $("#toast");
  clearTimeout(toastTimer);
  element.textContent = message;
  element.hidden = false;
  toastTimer = setTimeout(() => { element.hidden = true; }, 4500);
}

function metric(label, value, note, iconName, className = "") {
  const card = node("article", `metric-card ${className}`);
  const top = node("div", "metric-top");
  const mark = node("span", "metric-icon");
  mark.append(icon(iconName));
  top.append(node("h2", "metric-label", label), mark);
  const number = node("p", "metric-value", value);
  if (className === "profit" && state.report?.totals.profit < 0) number.classList.add("text-negative");
  card.append(top, number, node("p", "metric-note", note));
  return card;
}

function heading(title, subtitle) {
  const header = node("div", "panel-heading");
  const text = node("div");
  text.append(node("h2", "", title));
  if (subtitle) text.append(node("p", "panel-subtitle", subtitle));
  header.append(text);
  return header;
}

function chart(daily, totals) {
  const panel = node("section", "panel");
  const header = heading("Динамика выручки", periodText());
  const legend = node("span", "chart-legend");
  legend.append(node("span"), document.createTextNode("Выручка"));
  header.append(legend);
  panel.append(header);
  const container = node("div", "chart-container");
  if (!daily.length || !daily.some(day => Number(day.revenue))) {
    empty(container, "В этом периоде нет продаж", "Демонстрационные продажи доступны с 1 по 14 октября 2026. Выберите эти даты в фильтре.");
  } else {
    const width = 640, height = 250;
    const plot = { left: 61, top: 17, right: 617, bottom: 209 };
    const max = Math.max(...daily.map(day => Number(day.revenue)), 1) * 1.18;
    const point = (day, index) => [daily.length === 1 ? (plot.left + plot.right) / 2 : plot.left + index * (plot.right - plot.left) / (daily.length - 1), plot.bottom - (Number(day.revenue) / max) * (plot.bottom - plot.top)];
    const svg = svgNode("svg", { viewBox: `0 0 ${width} ${height}`, class: "revenue-chart", role: "img", "aria-label": `Выручка по дням. За период: ${currency(totals.revenue)}.` });
    const defs = svgNode("defs");
    const gradient = svgNode("linearGradient", { id: "chart-fill", x1: "0", y1: "0", x2: "0", y2: "1" });
    gradient.append(svgNode("stop", { offset: "0%", "stop-color": "#80a3ff", "stop-opacity": ".22" }), svgNode("stop", { offset: "100%", "stop-color": "#80a3ff", "stop-opacity": ".01" }));
    defs.append(gradient);
    svg.append(defs);
    for (let tick = 0; tick <= 3; tick++) {
      const value = max * tick / 3;
      const y = plot.bottom - tick * (plot.bottom - plot.top) / 3;
      const label = value >= 1000 ? `${percentFormatter.format(value / 1000)} тыс.` : integer(value);
      svg.append(svgNode("line", { x1: plot.left, y1: y, x2: plot.right, y2: y, stroke: "#edf1f8", "stroke-width": "1", "stroke-dasharray": "3 5" }), svgNode("text", { x: plot.left - 12, y: y + 4, "text-anchor": "end" }, label));
    }
    const points = daily.map(point);
    const line = points.map(([x, y], index) => `${index ? "L" : "M"}${x},${y}`).join(" ");
    svg.append(svgNode("path", { d: `${line} L${points.at(-1)[0]},${plot.bottom} L${points[0][0]},${plot.bottom} Z`, fill: "url(#chart-fill)" }));
    svg.append(svgNode("path", { d: line, fill: "none", stroke: "#5b87ef", "stroke-width": "2.6", "stroke-linecap": "round", "stroke-linejoin": "round" }));
    daily.forEach((day, index) => {
      const [x, y] = points[index];
      const circle = svgNode("circle", { cx: x, cy: y, r: "3.5", fill: "#fff", stroke: "#5b87ef", "stroke-width": "2", tabindex: "0", "aria-label": `${shortDate(day.date)}: ${currency(day.revenue)}` });
      circle.append(svgNode("title", {}, `${dateText(day.date)} — ${currency(day.revenue)}`));
      svg.append(circle);
    });
    [...new Set([0, Math.floor((daily.length - 1) / 2), daily.length - 1])].forEach(index => {
      const [x] = points[index];
      svg.append(svgNode("text", { x, y: plot.bottom + 26, "text-anchor": "middle" }, shortDate(daily[index].date)));
    });
    container.append(svg);
  }
  panel.append(container, node("p", "chart-footnote", "Продажи показаны по дням. Суммы рассчитаны на учебном наборе данных."));
  return panel;
}

function breakdown(totals) {
  const panel = node("section", "panel");
  panel.append(heading("Из чего складывается прибыль", "Все удержания и расходы за период"));
  const rows = node("div", "breakdown");
  const values = [
    ["Выручка", totals.revenue, false], ["Комиссия Wildberries", totals.commission, true], ["Логистика", totals.logistics, true],
    ["Себестоимость товаров", totals.cost, true], ["Дополнительные расходы", totals.expenses, true], ["Чистая прибыль", totals.profit, false]
  ];
  values.forEach(([label, value, subtract], index) => {
    const row = node("div", `breakdown-row${index === values.length - 1 ? " total" : ""}`);
    const number = node("strong", Number(value) < 0 ? "text-negative" : "", `${subtract && Number(value) > 0 ? "− " : ""}${currency(value)}`);
    row.append(node("span", "", label), number);
    rows.append(row);
  });
  panel.append(rows, node("p", "formula-note", "Чистая прибыль = выручка − комиссия − логистика − себестоимость − дополнительные расходы."));
  return panel;
}

function renderOverview(report, restoreFocus = false) {
  state.report = report;
  const toolbar = node("div", "section-toolbar");
  const form = node("form", "period-form");
  form.setAttribute("aria-label", "Период отчёта");
  const range = node("div", "date-control");
  const from = node("input"), to = node("input");
  from.type = to.type = "date";
  from.required = to.required = true;
  from.id = "date-from";
  to.id = "date-to";
  from.setAttribute("aria-label", "Начало периода");
  to.setAttribute("aria-label", "Окончание периода");
  from.value = state.from;
  to.value = state.to;
  range.append(from, node("span", "", "—"), to);
  const apply = button("Применить", "button-secondary");
  apply.type = "submit";
  apply.id = "apply-period";
  form.append(range, apply);
  const exportLink = node("a", "button button-secondary export-button");
  exportLink.href = `/api/export?${new URLSearchParams({ date_from: state.from, date_to: state.to })}`;
  exportLink.setAttribute("download", "");
  exportLink.append(icon("download"), document.createTextNode("Экспорт CSV"));
  toolbar.append(form, exportLink);
  const errorBox = node("p", "form-error date-error");
  errorBox.setAttribute("role", "alert");
  errorBox.hidden = true;
  form.addEventListener("submit", async event => {
    event.preventDefault();
    errorBox.hidden = true;
    if (from.value > to.value) {
      errorBox.textContent = "Начало периода должно быть раньше окончания.";
      errorBox.hidden = false;
      from.focus();
      return;
    }
    apply.disabled = true;
    apply.textContent = "Загрузка…";
    const oldFrom = state.from, oldTo = state.to;
    state.from = from.value;
    state.to = to.value;
    try {
      const nextReport = await api(reportPath());
      state.report = nextReport;
      if (state.view === "overview") renderOverview(nextReport, true);
    } catch (error) {
      state.from = oldFrom;
      state.to = oldTo;
      errorBox.textContent = error.message;
      errorBox.hidden = false;
      apply.disabled = false;
      apply.textContent = "Применить";
    }
  });
  const totals = report.totals;
  const metrics = node("div", "metrics-grid");
  metrics.append(metric("Выручка", currency(totals.revenue), "Общая сумма продаж", "chart"), metric("Чистая прибыль", currency(totals.profit), "После всех расходов", "wallet", "profit"), metric("Продано товаров", `${integer(totals.units)} шт.`, "За выбранный период", "box"), metric("Маржинальность", percent(totals.margin), "Доля прибыли в выручке", "chart"));
  const grid = node("div", "overview-grid");
  grid.append(chart(report.daily, totals), breakdown(totals));
  const note = node("p", "overview-note");
  note.append(icon("info"), document.createTextNode("Расходы можно добавлять на отдельной вкладке. Они автоматически учитываются в прибыли по дате записи."));
  content.replaceChildren(toolbar, errorBox, metrics, grid, note);
  content.setAttribute("aria-busy", "false");
  if (restoreFocus) apply.focus();
}

function createTable(labels) {
  const scroll = node("div", "table-scroll");
  scroll.tabIndex = 0;
  scroll.setAttribute("role", "region");
  scroll.setAttribute("aria-label", "Таблица, прокручивается по горизонтали");
  const table = node("table", "data-table");
  const thead = node("thead"), tr = node("tr"), body = node("tbody");
  labels.forEach(([label, numeric]) => {
    const cell = node("th", numeric ? "numeric" : "", label);
    cell.scope = "col";
    tr.append(cell);
  });
  thead.append(tr);
  table.append(thead, body);
  scroll.append(table);
  return { scroll, body };
}

function productCell(product) {
  const cell = node("td");
  const open = node("button", "product-open product-name");
  open.type = "button";
  open.setAttribute("aria-label", `Открыть товар: ${product.name}`);
  const thumb = node("span", "product-thumb");
  thumb.append(icon("box"));
  const text = node("div");
  text.append(node("strong", "", product.name), node("small", "", `Артикул ${product.sku}`));
  open.append(thumb, text);
  open.addEventListener("click", () => openProduct(product.sku));
  cell.append(open);
  return cell;
}

function renderProductRows(target, products, clearSearch) {
  target.setAttribute("aria-busy", "false");
  if (!products.length) {
    empty(target, "Товары не найдены", "Попробуйте другое название или артикул. Можно очистить поиск, чтобы увидеть весь каталог.", button("Сбросить поиск", "button-secondary", clearSearch));
    return;
  }
  const { scroll, body } = createTable([["Товар", false], ["Цена", true], ["Себест.", true], ["Остаток", true], ["Продано", true], ["Выручка", true], ["Прибыль", true], ["Маржа", true]]);
  products.forEach(product => {
    const row = node("tr");
    row.append(productCell(product), node("td", "numeric", currency(product.price)), node("td", "numeric", currency(product.cost)));
    const stock = node("td", "numeric");
    stock.append(node("span", "stock-pill", `${integer(product.stock)} шт.`));
    row.append(stock, node("td", "numeric", `${integer(product.units_sold)} шт.`), node("td", "numeric", currency(product.revenue)), node("td", `numeric ${product.profit < 0 ? "text-negative" : "text-positive"}`, currency(product.profit)), node("td", "numeric", percent(product.margin)));
    body.append(row);
  });
  target.replaceChildren(scroll);
}

async function loadProducts() {
  const panel = node("section", "panel table-panel");
  const intro = node("div", "table-intro");
  const title = node("div");
  const count = node("p", "table-count", "Продажи за 1–14 октября 2026");
  title.append(node("h2", "", "Каталог Wildberries"), count);
  const search = node("label", "search-control");
  const input = node("input");
  input.type = "search";
  input.placeholder = "Название или артикул";
  input.setAttribute("aria-label", "Поиск по названию или артикулу");
  search.append(icon("search"), input);
  intro.append(title, search);
  const body = node("div");
  const note = node("p", "table-note", "Прибыль по товару учитывает комиссию, логистику и себестоимость. Общие расходы магазина вычитаются на вкладке «Сводка».");
  panel.append(intro, body, note);
  content.replaceChildren(panel);
  content.setAttribute("aria-busy", "false");
  let requestId = 0;
  const run = async () => {
    const id = ++requestId;
    productController?.abort();
    productController = new AbortController();
    loading(body, "Ищем товары…");
    try {
      const data = await api(`/api/products?${new URLSearchParams({ q: input.value.trim() })}`, { signal: productController.signal });
      if (id !== requestId || state.view !== "products") return;
      count.textContent = `${integer(data.items.length)} ${plural(data.items.length, "товар", "товара", "товаров")} · продажи за 1–14 октября 2026`;
      renderProductRows(body, data.items, () => { input.value = ""; run(); input.focus(); });
    } catch (error) {
      if (error.name !== "AbortError" && id === requestId) failure(body, error, run);
    }
  };
  input.addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(run, 220); });
  await run();
}

async function openProduct(sku) {
  const request = ++productDetailRequest;
  const dialog = $("#product-dialog"), target = $("#product-content");
  loading(target, "Загружаем карточку товара…");
  // Keep the dialog's accessible name present while the request is in flight.
  const title = node("h2", "sr-only", "Карточка товара");
  title.id = "product-title";
  target.prepend(title);
  dialog.showModal();
  try {
    const product = await api(`/api/products/${encodeURIComponent(sku)}`);
    if (request !== productDetailRequest || !dialog.open) return;
    const name = node("h2", "", product.name);
    name.id = "product-title";
    const details = node("dl", "product-details");
    [["Цена продажи", currency(product.price)], ["Себестоимость", currency(product.cost)], ["Остаток", `${integer(product.stock)} шт.`], ["Продано за период", `${integer(product.units_sold)} шт.`], ["Выручка", currency(product.revenue)], ["Прибыль", currency(product.profit)], ["Маржинальность", percent(product.margin)]].forEach(([label, value]) => {
      const item = node("div", `product-detail${label === "Прибыль" ? " profit" : ""}`);
      const amount = node("dd", label === "Прибыль" && product.profit < 0 ? "text-negative" : "", value);
      item.append(node("dt", "", label), amount);
      details.append(item);
    });
    target.replaceChildren(name, node("p", "product-sku", `Артикул ${product.sku} · Wildberries`), details, node("p", "form-note", "Продажи за 1–14 октября 2026. Прибыль товара указана до общих расходов магазина."));
    target.setAttribute("aria-busy", "false");
  } catch (error) {
    if (request !== productDetailRequest || !dialog.open) return;
    failure(target, error);
    const name = node("h2", "sr-only", "Карточка товара");
    name.id = "product-title";
    target.prepend(name);
  }
}

function openExpense() {
  const form = $("#expense-form");
  form.reset();
  $("#expense-date").value = "2026-10-14";
  $("#expense-error").hidden = true;
  $("#expense-dialog").showModal();
  $("#expense-date").focus();
}

async function refreshAfterMutation() {
  state.report = null;
  const reportPromise = api(reportPath()).then(data => { state.report = data; }).catch(() => { state.report = null; });
  if (state.view === "expenses") await loadExpenses();
  await reportPromise;
}

async function deleteExpense(expense, trigger) {
  trigger.disabled = true;
  try {
    await api(`/api/expenses/${encodeURIComponent(expense.id)}`, { method: "DELETE" });
    toast("Расход удалён. Прибыль пересчитана.");
    await refreshAfterMutation();
  } catch (error) {
    trigger.disabled = false;
    toast(error.message);
  }
}

async function loadExpenses() {
  if (state.view !== "expenses") return;
  loading(content, "Загружаем расходы…");
  try {
    const data = await api("/api/expenses");
    if (state.view !== "expenses") return;
    const toolbar = node("div", "section-toolbar");
    const label = node("span", "toolbar-label", "Все расходы магазина");
    toolbar.append(label, button("Добавить расход", "button-primary", openExpense, "plus"));
    const panel = node("section", "panel table-panel");
    const intro = node("div", "table-intro");
    const title = node("div");
    title.append(node("h2", "", "Дополнительные расходы"), node("p", "table-count", `${integer(data.items.length)} ${plural(data.items.length, "запись", "записи", "записей")} · за всё время`));
    const summary = node("div", "expense-summary");
    summary.append(node("span", "", "Всего расходов"), node("strong", "", currency(data.items.reduce((sum, item) => sum + Number(item.amount), 0))));
    intro.append(title, summary);
    panel.append(intro);
    if (!data.items.length) {
      const body = node("div");
      empty(body, "Расходов пока нет", "Добавьте рекламу, упаковку или другие затраты. Они появятся в расчёте чистой прибыли.", button("Добавить первый расход", "button-secondary", openExpense, "plus"));
      panel.append(body);
    } else {
      const { scroll, body } = createTable([["Дата", false], ["Категория", false], ["Комментарий", false], ["Сумма", true], ["", false]]);
      data.items.forEach(expense => {
        const row = node("tr");
        const category = node("td");
        category.append(node("span", "expense-category", expense.category));
        const deleteCell = node("td", "numeric");
        const remove = node("button", "icon-button danger");
        remove.type = "button";
        remove.setAttribute("aria-label", `Удалить расход: ${expense.category}, ${currency(expense.amount)}, ${dateText(expense.date)}`);
        remove.title = "Удалить расход";
        remove.append(icon("trash"));
        remove.addEventListener("click", () => deleteExpense(expense, remove));
        deleteCell.append(remove);
        row.append(node("td", "", shortDate(expense.date)), category, node("td", "expense-comment", expense.comment || "—"), node("td", "numeric", currency(expense.amount)), deleteCell);
        body.append(row);
      });
      panel.append(scroll);
    }
    panel.append(node("p", "table-note", "В сводке учитываются только расходы с датой внутри выбранного периода. Новые записи сохраняются в учебном проекте."));
    content.replaceChildren(toolbar, panel);
    content.setAttribute("aria-busy", "false");
  } catch (error) {
    if (state.view === "expenses") failure(content, error, loadExpenses);
  }
}

$("#expense-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const submit = $("#expense-submit"), errorBox = $("#expense-error");
  errorBox.hidden = true;
  submit.disabled = true;
  submit.textContent = "Сохраняем…";
  const fields = form.elements;
  const payload = { date: fields.namedItem("date").value, category: fields.namedItem("category").value, amount: fields.namedItem("amount").valueAsNumber, comment: fields.namedItem("comment").value.trim() };
  try {
    await api("/api/expenses", { method: "POST", body: JSON.stringify(payload) });
    $("#expense-dialog").close();
    toast("Расход добавлен. Прибыль пересчитана.");
    await refreshAfterMutation();
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
  } finally {
    submit.disabled = false;
    submit.textContent = "Добавить расход";
  }
});

document.querySelectorAll("[data-close]").forEach(close => close.addEventListener("click", () => $(`#${close.dataset.close}`).close()));

async function navigate() {
  const requested = location.hash.slice(1);
  state.view = Object.hasOwn(titles, requested) ? requested : "overview";
  clearTimeout(searchTimer);
  productController?.abort();
  const [breadcrumb, title, description] = titles[state.view];
  $("#breadcrumb-title").textContent = breadcrumb;
  $("#page-title").textContent = title;
  $("#page-description").textContent = description;
  document.title = `LAITYS · ${breadcrumb}`;
  document.querySelectorAll("[data-view]").forEach(link => {
    const active = link.dataset.view === state.view;
    link.classList.toggle("active", active);
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  if (state.view === "products") { await loadProducts(); return; }
  if (state.view === "expenses") { await loadExpenses(); return; }
  loading(content, "Готовим сводку магазина…");
  try {
    const report = state.report || await api(reportPath());
    if (state.view === "overview") renderOverview(report);
  } catch (error) {
    if (state.view === "overview") failure(content, error, navigate);
  }
}

window.addEventListener("hashchange", navigate);
api("/version").then(data => { $("#app-version").textContent = `Study Edition · v${data.version}`; }).catch(() => {});
navigate();
