(() => {
  const form = document.querySelector("[data-registration-form]");
  if (!form) return;

  const role = form.querySelector('[name="role"]');
  const username = form.querySelector('[name="username"]');
  const phone = form.querySelector('[name="phone"]');
  const usernameLabel = form.querySelector(`label[for="${username.id}"]`);
  const usernameHelp = document.getElementById(`${username.id}_helptext`);
  const phoneError = "请输入 11 位数字手机号";
  const studentError = "请输入 12 位数字学号";

  const feedback = (input) => form.querySelector(`[data-validation-error="${input.name}"]`);
  const validate = (input, message) => {
    input.setCustomValidity("");
    const valid = input.validity.valid;
    if (!valid && message) input.setCustomValidity(message);
    feedback(input).textContent = valid ? "" : message;
  };

  const syncRole = () => {
    const student = role.value === "student";
    username.setCustomValidity("");
    feedback(username).textContent = "";
    username.maxLength = student ? 12 : Number(username.dataset.accountMaxlength);
    username.placeholder = student ? "请输入 12 位学号" : "请输入工号或账号";
    usernameLabel.textContent = student ? "学号" : "学号 / 工号 / 账号";
    usernameHelp.textContent = student
      ? "学号须为 12 位半角数字（0–9），前导零会保留。"
      : username.dataset.accountHelp;
    if (student) {
      username.setAttribute("minlength", "12");
      username.setAttribute("pattern", "[0-9]{12}");
      username.setAttribute("inputmode", "numeric");
      username.setAttribute("title", studentError);
    } else {
      for (const attribute of ["minlength", "pattern", "inputmode", "title"]) {
        username.removeAttribute(attribute);
      }
    }
  };

  for (const event of ["input", "invalid"]) {
    phone.addEventListener(event, () => validate(phone, phoneError));
    username.addEventListener(event, () => validate(
      username, role.value === "student" ? studentError : ""
    ));
  }
  role.addEventListener("change", syncRole);
  syncRole();
})();
