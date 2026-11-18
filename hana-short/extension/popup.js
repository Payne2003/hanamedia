const $ = (id) => document.getElementById(id);
const show = (s) => { $("status").textContent = s ? `${s.text}\n${new Date(s.at).toLocaleTimeString()}` : "…"; };
const out = (o) => { $("out").hidden = false; $("out").textContent = JSON.stringify(o, null, 2); };

chrome.runtime.sendMessage({ type: "get" }, (r) => r && show(r.status));
chrome.runtime.onMessage.addListener((m) => { if (m.type === "status") show(m.status); });
$("one").onclick = () => chrome.runtime.sendMessage({ type: "start", auto: false });
$("all").onclick = () => chrome.runtime.sendMessage({ type: "start", auto: true });
$("stop").onclick = () => chrome.runtime.sendMessage({ type: "stop" });
$("hello").onclick = () => chrome.runtime.sendMessage({ type: "hello" }, out);
$("inspect").onclick = () => chrome.runtime.sendMessage({ type: "inspect" }, out);
