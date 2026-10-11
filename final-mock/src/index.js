// Mock of Freya Bank's statement services (service contract of 1 Oct 2026).
// Serves exactly the answers of the bank's test customers, status code included.
import customers from "./data.json";

const BASE = "/voice/v1/";
const SERVICES = ["cards.list", "cards.balance", "contact.email.get", "statement.pdf.send",
  "statement.week.get", "statement.week.decide", "statement.week.change"];
// 404 code per service (contract section 4). Services without a 404 answer 400 INVALID_ARGUMENTS_ERROR.
const NOT_FOUND = { "cards.list": "FB_IVR_CHANNEL_102", "cards.balance": "FB_IVR_CHANNEL_120",
  "statement.week.get": "FB_IVR_CHANNEL_121", "statement.pdf.send": "FB_IVR_CHANNEL_107" };
const NOT_FOUND_TEXT = { "cards.list": "Müşteriye ait geçerli kart bilgisi bulunamadı.",
  "cards.balance": "Kart bakiye servisi hatası: kart bulunamadı", "statement.week.get": "Kart bulunamadı",
  "statement.pdf.send": "Pdf dokümanı bulunamadı" };
const CORS = { "Access-Control-Allow-Origin": "*" };

const res = (status, body) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json; charset=utf-8", ...CORS } });
const guid = () => [...crypto.getRandomValues(new Uint8Array(16))].map((b) => b.toString(16).padStart(2, "0")).join("");
function err(status, code, text, type) {
  const g = guid();
  return res(status, { result: { guid: g, returnCode: code, messageText: text, code, message: text, type,
    details: { guid: g, rootCause: status === 500 ? "TechnicalError" : "NotFoundException", errorOrigin: "ivr-gateway-test:ivr-channel-v0" } } });
}
const bad = (field) => err(400, "INVALID_ARGUMENTS_ERROR", `${field} cannot be empty`, "ERROR");
const notFound = (service) => NOT_FOUND[service]
  ? err(404, NOT_FOUND[service], NOT_FOUND_TEXT[service], "WARNING")
  : err(400, "INVALID_ARGUMENTS_ERROR", "invalid arguments", "ERROR");
const isTemplate = (v) => typeof v === "string" && (v.includes("{{") || v.includes("%7B%7B"));
const blank = (v) => v == null || String(v).trim() === "";
const digits = (s) => String(s || "").replace(/\D/g, "");

function findCard(map, cardNumber) {
  if (map[cardNumber]) return map[cardNumber];
  const d = digits(cardNumber);
  if (d.length < 4) return undefined;
  const hits = Object.keys(map).filter((k) => digits(k).slice(-4) === d.slice(-4) && (d.length === 4 || digits(k).slice(0, 4) === d.slice(0, 4)));
  return hits.length === 1 ? map[hits[0]] : undefined;
}

async function handle(service, request) {
  let body = {};
  try { body = await request.json(); } catch (e) { body = {}; }
  const data = (body && body.data) || {};
  const needsCustomer = service === "cards.list" || service === "contact.email.get" || service === "statement.pdf.send";
  const needsCard = !["cards.list", "contact.email.get"].includes(service);
  const needsWeek = service === "statement.week.decide" || service === "statement.week.change";

  if (needsCustomer && blank(data.customerNo)) return bad("customerNo");
  if (needsCard && blank(data.cardNumber)) return bad("card number");
  if (needsWeek && blank(data.newStatementWeek)) return bad("newStatementWeek");
  if (service === "contact.email.get" && blank(data.intAddressInfoType)) return bad("intAddressInfoType");

  let customer;
  if (needsCustomer) {
    customer = customers[String(data.customerNo).trim()];
    if (isTemplate(data.customerNo)) customer = customers["90000001"];       // panel's "Test API Request" sends the template text
  } else {
    // services called with a card only: find the customer that owns this card
    const card = String(data.cardNumber);
    customer = Object.values(customers).find((c) => c.services[service] && findCard(c.services[service], card));
    if (isTemplate(card)) customer = customers["90000001"];
  }
  if (!customer) return notFound(service);
  let node = customer.services[service];
  if (!node) return notFound(service);

  if (!("http" in node)) {                                   // keyed by the card, then (decide/change) by the week
    const card = isTemplate(data.cardNumber) ? Object.keys(node)[0] : String(data.cardNumber);
    node = findCard(node, card);
    if (!node) return notFound(service);
    if (!("http" in node)) {
      const wk = isTemplate(data.newStatementWeek) ? Object.keys(node)[0] : String(data.newStatementWeek);
      node = node[wk];
      if (!node) return err(400, "INVALID_ARGUMENTS_ERROR", "invalid newStatementWeek", "ERROR");
    }
  }
  const dl = Number(node.delaySeconds || node.gecikmeSaniye || 0); if (dl) await new Promise((r) => setTimeout(r, Math.min(dl, 25) * 1000));
  return res(node.http || 200, node.response ?? node.yanit ?? {});
}

export default {
  async fetch(request) {
    const url = new URL(request.url);
    if (request.method === "OPTIONS") return new Response(null, { headers: { ...CORS, "Access-Control-Allow-Methods": "GET, POST, OPTIONS", "Access-Control-Allow-Headers": "*" } });
    if (url.pathname === "/" || url.pathname === "/health") return res(200, { ok: true, service: "freya-bank-mock" });
    if (url.pathname === "/oauth2/token") return res(200, { access_token: "mock-token", token_type: "Bearer", expires_in: 1800 });
    if (request.method === "POST" && url.pathname.startsWith(BASE)) {
      const service = url.pathname.slice(BASE.length);
      if (SERVICES.includes(service)) return handle(service, request);
    }
    return res(404, { error: "not found", paths: SERVICES.map((s) => "POST " + BASE + s) });
  },
};
