const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const script = fs.readFileSync(path.join(__dirname, "../../static/js/register.js"), "utf8");

// DOM doubles exercise attribute changes and event handlers without a browser
// dependency. Native format rejection is also covered by Django request tests.
function page(initialRole = "student") {
  function input(name, value = "") {
    const attributes = {};
    const listeners = {};
    return {
      name, id: `id_${name}`, value, dataset: {}, attributes,
      nativeValid: true, validationMessage: "",
      setAttribute(name, value) { attributes[name] = String(value); },
      removeAttribute(name) { delete attributes[name]; },
      setCustomValidity(message) { this.validationMessage = message; },
      get validity() { return { valid: this.nativeValid && !this.validationMessage }; },
      set maxLength(value) { attributes.maxlength = String(value); },
      get maxLength() { return Number(attributes.maxlength); },
      addEventListener(event, handler) { listeners[event] = handler; },
      emit(event) { listeners[event](); },
    };
  }
  const role = input("role", initialRole);
  const username = input("username", "000000000001");
  const phone = input("phone", "00000000001");
  username.dataset = { accountMaxlength: "150", accountHelp: "原有账号规则" };
  const label = { textContent: "" };
  const help = { textContent: "" };
  const usernameError = { textContent: "" };
  const phoneError = { textContent: "" };
  const selectors = {
    '[name="role"]': role,
    '[name="username"]': username,
    '[name="phone"]': phone,
    'label[for="id_username"]': label,
    '[data-validation-error="username"]': usernameError,
    '[data-validation-error="phone"]': phoneError,
  };
  const form = { querySelector: (selector) => selectors[selector] };
  vm.runInNewContext(script, {
    document: {
      querySelector: () => form,
      getElementById: () => help,
    },
  });
  return { role, username, phone, label, help, usernameError, phoneError };
}

test("student inputs have exact digit constraints and keep leading zeros", () => {
  const { username, label } = page();
  assert.equal(username.attributes.pattern, "[0-9]{12}");
  assert.equal(username.attributes.minlength, "12");
  assert.equal(username.maxLength, 12);
  assert.equal(username.attributes.inputmode, "numeric");
  assert.equal(username.placeholder, "请输入 12 位学号");
  assert.equal(username.value, "000000000001");
  assert.equal(label.textContent, "学号");
});

test("teacher and maintenance accounts retain their usual limits", () => {
  for (const role of ["teacher", "maintenance"]) {
    const { username, help } = page(role);
    assert.equal(username.maxLength, 150);
    assert.equal(username.attributes.pattern, undefined);
    assert.equal(username.attributes.minlength, undefined);
    assert.equal(username.attributes.inputmode, undefined);
    assert.equal(help.textContent, "原有账号规则");
  }
});

test("switching roles removes stale student errors and restores constraints", () => {
  const state = page();
  state.username.value = "teacher.account";
  state.username.nativeValid = false;
  state.username.emit("invalid");
  assert.equal(state.username.validationMessage, "请输入 12 位数字学号");
  assert.equal(state.usernameError.textContent, "请输入 12 位数字学号");
  state.role.value = "teacher";
  state.role.emit("change");
  assert.equal(state.username.validationMessage, "");
  assert.equal(state.usernameError.textContent, "");
  assert.equal(state.username.attributes.pattern, undefined);
  assert.equal(state.username.maxLength, 150);
  assert.equal(state.username.value, "teacher.account");
  state.role.value = "student";
  state.role.emit("change");
  assert.equal(state.username.attributes.pattern, "[0-9]{12}");
  assert.equal(state.username.maxLength, 12);
  assert.equal(state.username.value, "teacher.account");
});

test("phone validation shows a local error and clears it after correction", () => {
  const { phone, phoneError } = page("maintenance");
  phone.nativeValid = false;
  phone.emit("invalid");
  assert.equal(phone.validationMessage, "请输入 11 位数字手机号");
  assert.equal(phoneError.textContent, "请输入 11 位数字手机号");
  phone.nativeValid = true;
  phone.emit("input");
  assert.equal(phone.validationMessage, "");
  assert.equal(phoneError.textContent, "");
  assert.equal(phone.value, "00000000001");
});
