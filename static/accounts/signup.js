(() => {
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
      field?.querySelectorAll(".field-error").forEach((error) => error.remove());
      input.removeAttribute("aria-invalid");
    }, {once: true});
  });
})();
