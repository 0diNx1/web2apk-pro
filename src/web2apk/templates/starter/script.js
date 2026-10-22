let taps = 0;
document.getElementById("tap").addEventListener("click", () => {
  taps += 1;
  document.getElementById("out").textContent = "Tapped " + taps + " time" + (taps === 1 ? "" : "s");
});
