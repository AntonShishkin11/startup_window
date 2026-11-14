"use strict";

const byId = (id) => document.getElementById(id);
const profile = byId("profile");
const requestForm = byId("request-form");
const slotForm = byId("slot-form");
const notice = byId("notice");
let state;
let busy = false;

function element(tag, value, className) {
  const node = document.createElement(tag);
  if (value) node.textContent = value;
  if (className) node.className = className;
  return node;
}

function actionButton(label, action) {
  const button = element("button", label, "primary");
  button.type = "button";
  button.addEventListener("click", () => {
    button.disabled = true;
    action().finally(() => { button.disabled = false; });
  });
  return button;
}

function showNotice(message, isError = false) {
  notice.textContent = message;
  notice.classList.toggle("error", isError);
  notice.hidden = false;
}

function dateLabel(day) {
  return day.split("-").reverse().join(".");
}

function slotLabel(slot) {
  return `${dateLabel(slot.day)} · ${slot.starts}–${slot.ends}`;
}

async function api(endpoint, payload) {
  const options = payload === undefined ? {} : {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload),
  };
  const response = await fetch(`/api/${endpoint}`, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error);
  return data;
}

async function update(endpoint, payload, message) {
  if (busy) return false;
  busy = true;
  try {
    state = await api(endpoint, payload);
    render();
    showNotice(message);
    return true;
  } catch (error) {
    showNotice(error.message, true);
    return false;
  } finally {
    busy = false;
  }
}

function render() {
  const names = new Map(state.customers.map((customer) => [customer.id, customer.name]));
  byId("requests-summary").textContent = `Заявок в листе ожидания: ${state.requests.length}`;
  const slotList = byId("slots");
  slotList.replaceChildren();
  if (!state.slots.length) slotList.append(element("p", "Добавьте первое свободное окно.", "empty"));

  for (const slot of state.slots) {
    const card = element("article", "", "item");
    const head = element("div", "", "item-head");
    const offers = state.offers.filter((offer) => offer.slot_id === slot.id);
    const confirmed = slot.confirmed_id !== null;
    head.append(element("strong", state.services[slot.service]),
      element("span", confirmed ? "Подтверждено" : "Открыто", confirmed ? "badge done" : "badge"));
    card.append(head, element("p", slotLabel(slot)));
    if (confirmed) {
      card.append(element("p", `Запись подтверждена: ${names.get(slot.confirmed_id)}`));
    } else {
      card.append(element("p", offers.length ? `Подходящих клиентов: ${offers.length}` : "Подходящих заявок пока нет."));
      for (const offer of offers) {
        const line = element("div", "", "offer-line");
        const name = names.get(offer.customer_id);
        line.append(element("span", `${name} · ${offer.accepted ? "отклик получен" : "предложение отправлено"}`));
        if (offer.accepted) {
          line.append(actionButton(`Подтвердить: ${name}`, () => update("confirm", offer, "Запись подтверждена. Окно закрыто.")));
        }
        card.append(line);
      }
    }
    slotList.append(card);
  }

  const inbox = byId("inbox");
  const customerId = Number(profile.value);
  inbox.replaceChildren();
  for (const offer of state.offers.filter((item) => item.customer_id === customerId)) {
    const slot = state.slots.find((item) => item.id === offer.slot_id);
    const card = element("article", "", "item");
    card.append(element("strong", state.services[slot.service]), element("p", slotLabel(slot)));
    if (slot.confirmed_id === customerId) {
      card.append(element("span", "Ваша запись подтверждена", "badge done"));
    } else if (slot.confirmed_id !== null) {
      card.append(element("span", "Окно уже занято", "badge"));
    } else if (offer.accepted) {
      card.append(element("span", "Ожидает подтверждения", "badge"));
    } else {
      card.append(actionButton("Мне подходит", () => update("respond", offer, "Отклик отправлен администратору.")));
    }
    inbox.append(card);
  }
  if (!inbox.childElementCount) inbox.append(element("p", "Подходящих предложений пока нет.", "empty"));
}

function loadRequestForm() {
  const item = state.requests.find((request) => request.customer_id === Number(profile.value));
  requestForm.elements.service.value = item?.service ?? "trim";
  requestForm.elements.day.value = item?.day ?? state.demoDay;
  requestForm.elements.starts.value = item?.starts ?? "16:00";
  requestForm.elements.ends.value = item?.ends ?? "19:00";
}

function setView(view) {
  const admin = view === "admin";
  byId("admin").hidden = !admin;
  byId("client").hidden = admin;
  byId("hero-label").textContent = admin ? "КАБИНЕТ САЛОНА" : "КАБИНЕТ КЛИЕНТА";
  byId("hero-title").textContent = admin ? "Свободные окна" : "Лист ожидания";
  byId("hero-description").textContent = admin
    ? "Добавьте время после отмены. «Окно» подберёт заявки по услуге и времени визита."
    : "Укажите удобное время и откликнитесь, если в салоне появится подходящее окно.";
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.view === view));
  });
  notice.hidden = true;
}

async function start() {
  state = await api("state");
  for (const [id, label] of Object.entries(state.services)) {
    for (const form of [requestForm, slotForm]) form.elements.service.add(new Option(label, id));
  }
  for (const {id, name} of state.customers) profile.add(new Option(name, id));
  for (const form of [requestForm, slotForm]) form.elements.day.min = state.today;
  slotForm.elements.day.value = state.demoDay;
  slotForm.elements.starts.value = "16:00";
  slotForm.elements.ends.value = "17:30";
  loadRequestForm();
  render();

  document.querySelectorAll("[data-view]").forEach((button) => {
    button.addEventListener("click", () => setView(button.dataset.view));
  });
  profile.addEventListener("change", () => { loadRequestForm(); render(); });
  requestForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = Object.fromEntries(new FormData(requestForm));
    await update("request", {...payload, customer_id: Number(profile.value)}, "Заявка сохранена.");
  });
  slotForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    await update("slot", Object.fromEntries(new FormData(slotForm)), "Окно добавлено. Подбор выполнен.");
  });
  byId("demo").addEventListener("click", async () => {
    if ((state.slots.length || state.requests.length) &&
        !window.confirm("Заменить текущие заявки и окна демоданными?")) return;
    if (await update("demo", {}, "Загружены 4 учебные заявки на завтра.")) {
      slotForm.elements.service.value = "trim";
      slotForm.elements.day.value = state.demoDay;
      slotForm.elements.starts.value = "16:00";
      slotForm.elements.ends.value = "17:30";
      loadRequestForm();
    }
  });
}

start().catch((error) => showNotice(`Не удалось открыть приложение. ${error.message}`, true));
