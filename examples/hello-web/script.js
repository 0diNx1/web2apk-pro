(function () {
  var n = 0;
  var counter = document.getElementById("counter");
  counter.addEventListener("click", function () { n += 1; counter.textContent = "Count: " + n; });

  document.getElementById("file").addEventListener("change", function (e) {
    var f = e.target.files[0];
    document.getElementById("fileinfo").textContent = f ? f.name + " (" + f.size + " bytes)" : "";
  });

  // Optional native bridge (enabled by "js_bridge": true in web2apk.json). It only exists in the APK.
  var out = document.getElementById("bridge");
  var bridge = window.Web2ApkBridge;
  if (!bridge) { out.textContent = "bridge: not available (running in a normal browser)"; return; }
  var next = 1, pending = {};
  bridge.addEventListener("message", function (ev) {
    var msg = JSON.parse(ev.data);
    if (pending[msg.id]) { pending[msg.id](msg); delete pending[msg.id]; }
  });
  function call(method, args) {
    return new Promise(function (resolve) {
      var id = next++; pending[id] = resolve;
      bridge.postMessage(JSON.stringify({ id: id, method: method, args: args || {} }));
    });
  }
  call("getAppInfo").then(function (r) { out.textContent = "bridge: " + JSON.stringify(r.result); });
  document.getElementById("toast").addEventListener("click", function () {
    call("showToast", { text: "Hello from JavaScript" });
  });
})();
