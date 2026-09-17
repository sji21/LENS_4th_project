(() => {
  const password1 = document.getElementById("id_password1");
  const password2 = document.getElementById("id_password2");
  const mismatchError = document.querySelector("[data-password-match-error]");
  const password2Field = password2?.closest(".signup-field");
  const updatePasswordMatch = () => {
    if (!password1 || !password2 || !mismatchError || !password2Field) return;
    const mismatched = Boolean(password2.value) && password1.value !== password2.value;
    password2Field.classList.toggle("has-error", mismatched);
    mismatchError.hidden = !mismatched;
    password2.setAttribute("aria-invalid", mismatched ? "true" : "false");
  };
  password1?.addEventListener("input", updatePasswordMatch);
  password2?.addEventListener("input", updatePasswordMatch);

  const popup = document.getElementById("signup-error-popup");
  const close = document.getElementById("signup-error-close");
  if (!popup || !close) return;

  const dismiss = () => {
    popup.hidden = true;
    const firstInvalid = document.querySelector(".stack-form input[aria-invalid='true']");
    firstInvalid?.focus();
  };

  close.addEventListener("click", dismiss);
  popup.addEventListener("click", (event) => {
    if (event.target === popup) dismiss();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !popup.hidden) dismiss();
  });
  popup.querySelector(".signup-error-popup")?.focus();

  document.querySelectorAll(".signup-field input").forEach((input) => {
    input.addEventListener("input", () => {
      const field = input.closest(".signup-field");
      field?.classList.remove("has-error");
      field?.querySelectorAll(".field-error").forEach((error) => {
        if (!error.matches("[data-password-match-error]")) error.remove();
      });
      input.removeAttribute("aria-invalid");
      updatePasswordMatch();
    }, {once: true});
  });
})();
