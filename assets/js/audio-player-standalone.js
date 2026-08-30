document.addEventListener("DOMContentLoaded", () => {
  const cards = document.querySelectorAll("[data-audio-card]");

  cards.forEach((card) => {
    const audio = card.querySelector("audio");
    const status = card.querySelector("[data-audio-status]");

    if (!audio || !status) return;

    audio.addEventListener("play", () => {
      status.textContent = "در حال پخش";
    });

    audio.addEventListener("pause", () => {
      status.textContent = "متوقف شده";
    });

    audio.addEventListener("ended", () => {
      status.textContent = "پایان پخش";
    });

    audio.addEventListener("error", () => {
      status.textContent = "خطا در بارگذاری فایل صوتی";
    });
  });
});
