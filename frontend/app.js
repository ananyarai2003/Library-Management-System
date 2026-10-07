const $ = (id) => document.getElementById(id);

const keyInput = $("api-key");
const errorBox = $("error");

let books = [];
let members = [];

try {
  keyInput.value = sessionStorage.getItem("lms-api-key") || "";
} catch {
  /* storage unavailable: key just isn't remembered */
}
keyInput.addEventListener("input", () => {
  try {
    sessionStorage.setItem("lms-api-key", keyInput.value);
  } catch {
    /* ignore */
  }
});

function showError(message) {
  errorBox.textContent = message;
  errorBox.hidden = !message;
}

// Wraps fetch: adds the API key, parses JSON, and throws Error with the server's message.
async function api(path, { method = "GET", body } = {}) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && keyInput.value) headers["X-API-Key"] = keyInput.value;

  const res = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.status === 204) return null;

  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const err = data && data.error;
    let message = (err && err.message) || `Request failed (${res.status})`;
    if (err && Array.isArray(err.details) && err.details.length) {
      message += ": " + err.details.map((d) => d.msg || d.message || JSON.stringify(d)).join("; ");
    }
    throw new Error(message);
  }
  return data;
}

// Runs an action, shows any error, then refreshes the page data.
async function run(action) {
  try {
    showError("");
    await action();
    await refresh();
  } catch (e) {
    showError(e.message);
  }
}

function cell(tr, text) {
  const td = document.createElement("td");
  td.textContent = text;
  tr.appendChild(td);
  return td;
}

function button(label, className, onClick) {
  const b = document.createElement("button");
  b.textContent = label;
  b.className = className;
  b.addEventListener("click", onClick);
  return b;
}

function fillSelect(select, items, label) {
  select.replaceChildren();
  for (const item of items) {
    const opt = document.createElement("option");
    opt.value = item.id;
    opt.textContent = label(item);
    select.appendChild(opt);
  }
}

function renderBooks() {
  const list = $("book-list");
  list.replaceChildren();
  $("books-empty").hidden = books.length > 0;

  for (const b of books) {
    const tr = document.createElement("tr");
    cell(tr, b.title);
    cell(tr, b.author);
    cell(tr, b.isbn || "");
    const copies = cell(tr, "");
    const badge = document.createElement("span");
    badge.className = `badge ${b.available ? "in" : "out"}`;
    badge.textContent = `${b.available_copies}/${b.total_copies} available`;
    copies.appendChild(badge);

    const actions = document.createElement("td");
    actions.appendChild(
      button("Delete", "danger", () => {
        if (confirm(`Delete "${b.title}"?`)) run(() => api(`/api/books/${b.id}`, { method: "DELETE" }));
      })
    );
    tr.appendChild(actions);
    list.appendChild(tr);
  }
  fillSelect($("loan-book"), books.filter((b) => b.available), (b) => b.title);
}

function renderMembers() {
  const list = $("member-list");
  list.replaceChildren();
  $("members-empty").hidden = members.length > 0;
  for (const m of members) {
    const tr = document.createElement("tr");
    cell(tr, m.id);
    cell(tr, m.name);
    cell(tr, m.email);
    list.appendChild(tr);
  }
  fillSelect($("loan-member"), members, (m) => `${m.name} (${m.email})`);
}

function renderLoans(loans) {
  const list = $("loan-list");
  list.replaceChildren();
  $("loans-empty").hidden = loans.length > 0;

  const bookName = (id) => (books.find((b) => b.id === id) || {}).title || `#${id}`;
  const memberName = (id) => (members.find((m) => m.id === id) || {}).name || `#${id}`;

  for (const l of loans) {
    const tr = document.createElement("tr");
    cell(tr, bookName(l.book_id));
    cell(tr, memberName(l.member_id));
    cell(tr, new Date(l.due_at).toLocaleDateString());
    const status = cell(tr, "");
    const badge = document.createElement("span");
    badge.className = `badge ${l.overdue ? "out" : "in"}`;
    badge.textContent = l.overdue ? "Overdue" : "On loan";
    status.appendChild(badge);

    const actions = document.createElement("td");
    actions.appendChild(button("Return", "secondary", () => run(() => api(`/api/loans/${l.id}/return`, { method: "POST" }))));
    tr.appendChild(actions);
    list.appendChild(tr);
  }
}

async function refresh() {
  const q = $("search").value.trim();
  const [b, m, loans] = await Promise.all([
    api(`/api/books?limit=500${q ? `&q=${encodeURIComponent(q)}` : ""}`),
    api("/api/members?limit=500"),
    api("/api/loans?active=true&limit=500"),
  ]);
  books = b;
  members = m;
  renderBooks();
  renderMembers();
  renderLoans(loans);
}

$("book-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const isbn = $("isbn").value.trim();
  const body = {
    title: $("title").value.trim(),
    author: $("author").value.trim(),
    total_copies: Number($("copies").value) || 1,
  };
  if (isbn) body.isbn = isbn;
  run(async () => {
    await api("/api/books", { method: "POST", body });
    e.target.reset();
  });
});

$("search-form").addEventListener("submit", (e) => {
  e.preventDefault();
  run(async () => {});
});

$("member-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const body = { name: $("member-name").value.trim(), email: $("member-email").value.trim() };
  run(async () => {
    await api("/api/members", { method: "POST", body });
    e.target.reset();
  });
});

$("loan-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const body = { book_id: Number($("loan-book").value), member_id: Number($("loan-member").value) };
  run(() => api("/api/loans", { method: "POST", body }));
});

run(async () => {});
