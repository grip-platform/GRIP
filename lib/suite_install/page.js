(function () {
  "use strict";

  var body = document.body;
  var token = body.dataset.token;
  var stepNames = JSON.parse(body.dataset.steps);
  var list = document.getElementById("steps");
  var status = document.getElementById("status");
  var doneButton = document.getElementById("done");

  function post(path, payload) {
    return fetch(path, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Setup-Token": token
      },
      body: JSON.stringify(payload || {})
    }).then(function (response) {
      return response.json().then(function (data) {
        return { ok: response.ok, data: data };
      });
    });
  }

  function row(name) {
    var li = document.createElement("li");
    li.dataset.status = "not yet run";

    var label = document.createElement("span");
    label.className = "step-name";
    label.textContent = name;

    var detail = document.createElement("span");
    detail.className = "step-detail";
    detail.textContent = "not yet run";

    var button = document.createElement("button");
    button.type = "button";
    button.textContent = "Run";
    button.addEventListener("click", function () {
      button.disabled = true;
      detail.textContent = "running…";
      post("/run", { step: name }).then(function (result) {
        button.disabled = false;
        if (!result.ok) {
          li.dataset.status = "failed";
          detail.textContent = result.data.error || "the server could not run this step";
          return;
        }
        li.dataset.status = result.data.status;
        detail.textContent = result.data.detail;
        if (result.data.seal_error) {
          detail.textContent += " (not sealed: " + result.data.seal_error + ")";
        }
      }).catch(function () {
        button.disabled = false;
        li.dataset.status = "failed";
        detail.textContent = "the request itself failed";
      });
    });

    li.appendChild(label);
    li.appendChild(detail);
    li.appendChild(button);
    return li;
  }

  stepNames.forEach(function (name) {
    list.appendChild(row(name));
  });

  doneButton.addEventListener("click", function () {
    doneButton.disabled = true;
    status.textContent = "Stopping the setup server…";
    post("/done", {}).catch(function () {
      // The server may close the connection before replying; either way, it is done.
    });
  });

  var checkButton = document.getElementById("check");
  checkButton.addEventListener("click", function () {
    checkButton.disabled = true;
    status.textContent = "Checking the setup record…";
    post("/check", {}).then(function (result) {
      checkButton.disabled = false;
      status.textContent = result.data.detail || result.data.error || "the check itself failed";
    }).catch(function () {
      checkButton.disabled = false;
      status.textContent = "the request itself failed";
    });
  });
})();
