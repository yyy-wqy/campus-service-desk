document.querySelectorAll("[data-password-toggle]").forEach((button) => {
  button.addEventListener("click", () => {
    const input = button.closest(".field-control")?.querySelector("input");
    if (!input) return;

    const showing = input.type === "text";
    input.type = showing ? "password" : "text";
    button.setAttribute("aria-label", showing ? "显示密码" : "隐藏密码");
    button.classList.toggle("is-visible", !showing);
  });
});
