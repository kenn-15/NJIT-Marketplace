"use strict";

const $ = (selector) => document.querySelector(selector);
const money = (cents) => new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(cents / 100);
let products = [];
let cart;
try {
  cart = JSON.parse(localStorage.getItem("cart") || "{}");
  if (!cart || Array.isArray(cart) || typeof cart !== "object") cart = {};
} catch {
  cart = {};
}

function saveCart() {
  localStorage.setItem("cart", JSON.stringify(cart));
  renderCart();
}

function renderProducts(query = "") {
  const container = $("#products");
  container.replaceChildren();
  const matches = products.filter((p) => `${p.name} ${p.description}`.toLowerCase().includes(query.toLowerCase()));
  if (!matches.length) container.textContent = "No products found.";
  for (const product of matches) {
    const card = document.createElement("article");
    card.className = "product";
    const title = document.createElement("h3");
    title.textContent = product.name;
    const description = document.createElement("p");
    description.textContent = product.description;
    const price = document.createElement("p");
    price.textContent = money(product.price_cents);
    const button = document.createElement("button");
    button.textContent = "Add to cart";
    button.addEventListener("click", () => {
      cart[product.id] = Math.min(99, (cart[product.id] || 0) + 1);
      saveCart();
    });
    card.append(title, description, price, button);
    container.append(card);
  }
}

function renderCart() {
  const container = $("#cart");
  container.replaceChildren();
  let total = 0;
  for (const product of products) {
    const quantity = cart[product.id];
    if (!Number.isInteger(quantity) || quantity < 1 || quantity > 99) continue;
    total += product.price_cents * quantity;
    const item = document.createElement("div");
    item.className = "cart-item";
    const title = document.createElement("p");
    title.textContent = `${product.name} — ${money(product.price_cents)} each`;
    const input = document.createElement("input");
    input.type = "number";
    input.min = "1";
    input.max = "99";
    input.value = quantity;
    input.setAttribute("aria-label", `Quantity for ${product.name}`);
    input.addEventListener("change", () => {
      const value = Number(input.value);
      if (Number.isInteger(value) && value >= 1 && value <= 99) cart[product.id] = value;
      saveCart();
    });
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "Remove";
    remove.setAttribute("aria-label", `Remove ${product.name}`);
    remove.addEventListener("click", () => { delete cart[product.id]; saveCart(); });
    item.append(title, input, remove);
    container.append(item);
  }
  if (!container.childElementCount) container.textContent = "Your cart is empty.";
  $("#total").textContent = money(total);
  $("#pay").disabled = total === 0;
}

async function post(path, data) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Something went wrong.");
  return result;
}

$("#search").addEventListener("input", (event) => renderProducts(event.target.value.trim()));
$("#checkout").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("#pay").disabled = true;
  $("#message").textContent = "Starting PayPal checkout…";
  try {
    const items = products.filter((product) => Number.isInteger(cart[product.id]) && cart[product.id] >= 1 && cart[product.id] <= 99)
      .map((product) => ({ id: product.id, quantity: cart[product.id] }));
    const order = await post("/api/orders", { name: $("#name").value, email: $("#email").value, items });
    sessionStorage.setItem("pendingOrder", JSON.stringify({ id: order.id, access: order.access }));
    window.location.assign(order.approve_url);
  } catch (error) {
    $("#message").textContent = error.message;
    renderCart();
  }
});

async function start() {
  try {
    const response = await fetch("/api/products");
    if (!response.ok) throw new Error("Unable to load products.");
    products = await response.json();
    renderProducts();
    renderCart();
    const params = new URLSearchParams(location.search);
    if (params.get("checkout") === "cancel") {
      $("#message").textContent = "Checkout canceled. Your cart is still here.";
      sessionStorage.removeItem("pendingOrder");
      history.replaceState(null, "", "/");
    } else if (params.get("checkout") === "return") {
      const order = JSON.parse(sessionStorage.getItem("pendingOrder") || "null");
      if (!order || params.get("token") !== order.id) throw new Error("Could not find this checkout in your browser.");
      $("#pay").disabled = true;
      $("#message").textContent = "Confirming your payment…";
      await post("/api/orders/capture", order);
      cart = {};
      saveCart();
      sessionStorage.removeItem("pendingOrder");
      history.replaceState(null, "", "/");
      $("#message").textContent = "Payment complete. Thank you for your order!";
    }
  } catch (error) {
    $("#message").textContent = error.message + " You can refresh to retry.";
  }
}

start();
