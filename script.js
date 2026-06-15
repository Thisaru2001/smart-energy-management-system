const API_BASE = "http://localhost:2000";

// ======================== LOGIN / REGISTER (index.html) ========================
async function signinProcess() {
  const emailInput = document.querySelector('#loginForm input[type="email"]');
  const passwordInput = document.getElementById("loginPassword");
  if (!emailInput || !passwordInput) {
    alert("⚠️ Form fields not found.");
    return;
  }
  const email = emailInput.value.trim();
  const password = passwordInput.value.trim();
  if (!email || !password) {
    alert("Please fill in both fields.");
    return;
  }
  if (!/^\S+@\S+\.\S+$/.test(email)) {
    alert("Invalid email.");
    return;
  }
  if (password.length < 6) {
    alert("Password too short.");
    return;
  }
  await signInApiCall(email, password);
}

async function signInApiCall(email, password) {
  try {
    const resp = await fetch(`${API_BASE}/user/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    const data = await resp.json();
    if (resp.ok && data.token) {
      localStorage.setItem("token", data.token);
      localStorage.setItem("user", JSON.stringify(data.user));
      alert("Login successful!");
      // Redirect based on role
      const role = data.user.role;
      if (role === "energy_manager" || role === "system_admin") {
        window.location.href = "energy_manager.html";
      } else {
        window.location.href = "home_user.html";
      }
    } else {
      alert(data.detail || "Login failed");
    }
  } catch (e) {
    console.error(e);
    alert("Server error");
  }
}

function signupProcess() {
  const nameInput = document.querySelector('#registerForm input[type="text"]');
  const emailInput = document.querySelector(
    '#registerForm input[type="email"]',
  );
  const passwordInput = document.getElementById("registerPassword");
  if (!nameInput || !emailInput || !passwordInput) {
    alert("Form fields missing.");
    return;
  }
  const name = nameInput.value.trim();
  const email = emailInput.value.trim();
  const password = passwordInput.value.trim();
  if (!name || !email || !password) {
    alert("All fields required.");
    return;
  }
  if (!/^\S+@\S+\.\S+$/.test(email)) {
    alert("Invalid email.");
    return;
  }
  if (password.length < 6) {
    alert("Password too short.");
    return;
  }
  const hasUpper = /[A-Z]/.test(password);
  const hasNumber = /[0-9]/.test(password);
  const hasSymbol = /[!@#$%^&*()_\-+=\[\]{}|;:'",.<>?/`~]/.test(password);
  if (!hasUpper || !hasNumber || !hasSymbol) {
    alert("Password must contain uppercase, number, symbol.");
    return;
  }
  signupApiCall(name, email, password);
}

async function signupApiCall(name, email, password) {
  try {
    const resp = await fetch(`${API_BASE}/user/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, email, password, role: "home_user" }),
    });
    const data = await resp.json();
    if (resp.status === 200 || resp.status === 201) {
      alert("Registration successful!");
      window.location.href = "index.html";
    } else {
      alert("Registration failed: " + (data.detail || "Unknown error"));
    }
  } catch (e) {
    console.error(e);
    alert("Server error");
  }
}

// ======================== DASHBOARD LOGIC ========================
(function () {
  if (!document.getElementById("refreshBtn")) return; // nothing to do on login page

  const REFRESH_INTERVAL = 10000;
  const CHART_REFRESH_INTERVAL = 60000;

  let token = localStorage.getItem("token");
  let user = JSON.parse(localStorage.getItem("user") || "null");
  if (!token) {
    window.location.href = "index.html";
    return;
  }

  function getAuthHeaders() {
    return {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    };
  }

  async function fetchAPI(endpoint, options = {}) {
    const resp = await fetch(`${API_BASE}${endpoint}`, {
      ...options,
      headers: { ...getAuthHeaders(), ...options.headers },
    });
    if (!resp.ok) {
      if (resp.status === 401) {
        alert("Session expired.");
        localStorage.clear();
        window.location.href = "index.html";
      }
      throw new Error(`API ${resp.status}`);
    }
    return resp.json();
  }

  function timeAgo(dateStr) {
    const diff = Math.floor((Date.now() - new Date(dateStr).getTime()) / 1000);
    if (diff < 60) return "just now";
    if (diff < 3600) return Math.floor(diff / 60) + "m ago";
    if (diff < 86400) return Math.floor(diff / 3600) + "h ago";
    return Math.floor(diff / 86400) + "d ago";
  }

  function setText(sel, txt) {
    const el = document.querySelector(sel);
    if (el) el.textContent = txt;
  }
  function setHTML(sel, html) {
    const el = document.querySelector(sel);
    if (el) el.innerHTML = html;
  }

  async function updateMeterStatus() {
    try {
      const d = await fetchAPI("/analytics/dashboard/meter-status");
      setText(".nav-blur h4 span", d.meter_id);
      const badge = document.querySelector(".badge-green");
      if (badge)
        badge.innerHTML = `<span class="w-2 h-2 rounded-full bg-${d.status === "connected" ? "[#22c55e]" : "red-500"} animate-pulse"></span> ${d.status === "connected" ? "Connected" : "Disconnected"}`;
    } catch (e) {}
  }

  async function updateStats() {
    try {
      const d = await fetchAPI("/analytics/dashboard/stats");
      // usage
      const u = document.querySelector(".stat-card-dash:nth-child(1) .value");
      if (u)
        u.innerHTML = `${d.today_usage_kwh.toFixed(1)} <span class=\"text-base font-medium opacity-50\">kWh</span>`;
      setText(
        ".stat-card-dash:nth-child(1) .text-[#22c55e]",
        `${d.usage_change_percent >= 0 ? "▲" : "▼"} ${Math.abs(d.usage_change_percent).toFixed(1)}%`,
      );
      // cost
      setText(
        ".stat-card-dash:nth-child(2) .value",
        `$${d.estimated_cost.toFixed(2)}`,
      );
      setText(
        ".stat-card-dash:nth-child(2) .text-[#22c55e]",
        `${d.cost_change_percent >= 0 ? "▲" : "▼"} ${Math.abs(d.cost_change_percent).toFixed(1)}%`,
      );
      // carbon
      const c = document.querySelector(".stat-card-dash:nth-child(3) .value");
      if (c)
        c.innerHTML = `${d.carbon_saved_kg.toFixed(1)} <span class=\"text-base font-medium opacity-50\">kg</span>`;
      setText(
        ".stat-card-dash:nth-child(3) .text-[#22c55e]",
        `${d.carbon_change_percent >= 0 ? "▲" : "▼"} ${Math.abs(d.carbon_change_percent).toFixed(1)}%`,
      );
      // reliability
      const r = document.querySelector(".stat-card-dash:nth-child(4) .value");
      if (r)
        r.innerHTML = `${d.grid_reliability_percent.toFixed(2)}<span class=\"text-base font-medium opacity-50\">%</span>`;
      setText(
        ".stat-card-dash:nth-child(4) .text-[#22c55e]",
        `${d.reliability_change_percent >= 0 ? "▲" : "▼"} ${Math.abs(d.reliability_change_percent).toFixed(2)}%`,
      );
    } catch (e) {}
  }

  let energyChartInstance = null; // keep a reference to destroy old chart

  async function updateChart() {
    try {
      const data = await fetchAPI("/analytics/dashboard/energy-chart");
      const days = data.daily;

      // Prepare labels and values
      const labels = days.map((d) =>
        new Date(d.date).toLocaleDateString("en-US", { weekday: "short" }),
      );
      const values = days.map((d) => d.energy_kwh);

      // Update total and forecast text
      const total = days.reduce((s, d) => s + d.energy_kwh, 0);
      const totalSpan = document.getElementById("weekTotal");
      if (totalSpan)
        totalSpan.textContent = `Total: ${total.toFixed(1)} kWh this week`;

      const forecastSpan = document.getElementById("forecastNextDay");
      if (forecastSpan && data.forecast_next_day) {
        forecastSpan.textContent = `Forecast next day: ${data.forecast_next_day.toFixed(1)} kWh`;
      }

      // Draw chart
      const ctx = document.getElementById("energyChart").getContext("2d");

      // Destroy old chart if exists
      if (energyChartInstance) energyChartInstance.destroy();

      energyChartInstance = new Chart(ctx, {
        type: "line",
        data: {
          labels: labels,
          datasets: [
            {
              label: "Energy (kWh)",
              data: values,
              borderColor: "#22c55e", // green line
              backgroundColor: "rgba(34, 197, 94, 0.1)", // light green fill
              borderWidth: 3,
              tension: 0.4, // smooth curves
              fill: true,
              pointBackgroundColor: "#22c55e",
              pointBorderColor: "#ffffff",
              pointBorderWidth: 2,
              pointRadius: 5,
              pointHoverRadius: 7,
            },
            // Forecast dot for next day (optional)
            ...(data.forecast_next_day
              ? [
                  {
                    label: "Forecast",
                    data: [...Array(6).fill(null), data.forecast_next_day], // only last point
                    borderColor: "#86efac",
                    backgroundColor: "rgba(134, 239, 172, 0.15)",
                    borderWidth: 2,
                    borderDash: [5, 5],
                    tension: 0.4,
                    fill: false,
                    pointBackgroundColor: "#86efac",
                    pointBorderColor: "#ffffff",
                    pointBorderWidth: 2,
                    pointRadius: 6,
                    pointHoverRadius: 8,
                  },
                ]
              : []),
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: {
              display: true,
              labels: {
                usePointStyle: true,
                boxWidth: 8,
                font: { size: 11 },
              },
            },
            tooltip: {
              backgroundColor: "#0f2b1a",
              titleColor: "#ffffff",
              bodyColor: "#d1fae5",
            },
          },
          scales: {
            y: {
              beginAtZero: true,
              grid: { color: "rgba(34, 197, 94, 0.08)" },
              ticks: { callback: (v) => v + " kWh", font: { size: 10 } },
            },
            x: {
              grid: { display: false },
              ticks: { font: { size: 10 } },
            },
          },
        },
      });
    } catch (e) {
      console.error("Chart update error:", e);
    }
  }

  async function updateDevices() {
    try {
      const devices = await fetchAPI("/analytics/dashboard/devices");
      const container = document.getElementById("deviceList");
      if (!container) return;
      container.innerHTML = "";
      devices.forEach((d) => {
        container.innerHTML += `
          <div class="device-item">
            <div class="flex items-center gap-3">
              <div class="w-8 h-8 rounded-lg bg-[#dcfce7] flex items-center justify-center text-[#22c55e]"><svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M12 6v6m0 0v6m0-6h6m-6 0H6"/></svg></div>
              <div><p class="text-sm font-semibold">${d.name}</p><p class="text-xs opacity-60">${d.location || ""}</p></div>
            </div>
            <span class="badge-${d.status === "On" ? "green" : "warning"}">${d.status}</span>
          </div>`;
      });
      if (!document.getElementById("viewAllDevicesBtn")) {
        const btn = document.createElement("button");
        btn.id = "viewAllDevicesBtn";
        btn.className =
          "w-full mt-3 py-2.5 rounded-xl bg-[#f0fdf4] border border-[#22c55e]/20 text-[#22c55e] text-sm font-semibold hover:bg-[#dcfce7]";
        btn.textContent = "View All Devices →";
        container.parentNode.appendChild(btn);
      }
    } catch (e) {}
  }

  async function updateActivity() {
    try {
      const acts = await fetchAPI("/analytics/dashboard/activity");
      const container = document.querySelector(".chart-container .space-y-1");
      if (!container) return;
      container.innerHTML = acts
        .slice(0, 4)
        .map(
          (a) => `
        <div class="activity-item">
          <div class="w-9 h-9 rounded-full bg-[#dcfce7] flex items-center justify-center text-[#22c55e] flex-shrink-0"><svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path d="M13 10V3L4 14h7v7l9-11h-7z"/></svg></div>
          <div class="flex-1"><p class="text-sm font-medium">${a.message}</p><p class="text-xs opacity-60">${timeAgo(a.timestamp)}</p></div>
          <span class="text-xs opacity-40">${timeAgo(a.timestamp)}</span>
        </div>`,
        )
        .join("");
    } catch (e) {}
  }

  async function updateTips() {
    try {
      const tips = await fetchAPI("/analytics/dashboard/tips");
      const container = document.querySelector(".chart-container .space-y-4");
      if (!container) return;
      container.innerHTML = tips
        .slice(0, 3)
        .map(
          (t) => `
        <div class="bg-[#f0fdf4] rounded-xl p-4 border border-[#22c55e]/10">
          <div class="flex items-start gap-3">
            <div class="w-7 h-7 rounded-full bg-[#22c55e]/10 flex items-center justify-center text-[#22c55e] flex-shrink-0 mt-0.5"><svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg></div>
            <div><p class="text-sm font-semibold">${t.title}</p><p class="text-xs opacity-70">${t.description}</p></div>
          </div>
        </div>`,
        )
        .join("");
    } catch (e) {}
  }

  async function updateNotificationCount() {
    try {
      const notifs = await fetchAPI("/notification/notifications?limit=100");
      const badge = document.querySelector(".absolute.-top-1.-right-1");
      if (badge) {
        badge.textContent = notifs.length;
        badge.style.display = notifs.length > 0 ? "flex" : "none";
      }
    } catch (e) {}
  }

  function updateUserInfo() {
    if (user) {
      // Update the big welcome heading
      const heading = document.querySelector("h1");
      if (heading) {
        heading.innerHTML = `Welcome back, ${user.name} 👋`;
      }
      // Update the top nav name
      setText(".hidden.sm\\:block.text-sm", user.name);
      // Update avatar initials
      const initials = user.name
        .split(" ")
        .map((n) => n[0])
        .join("")
        .toUpperCase()
        .slice(0, 2);
      setText(".w-9.h-9.rounded-full.bg-gradient-to-br", initials);
    } else if (token) {
      try {
        const payload = JSON.parse(atob(token.split(".")[1]));
        setText(".hidden.sm\\:block.text-sm", payload.email);
      } catch (e) {}
    }
  }

  async function loadAllData() {
    updateUserInfo();
    updateMeterStatus();
    updateStats();
    updateChart();
    //updateDevices();
    updateActivity();
    updateTips();
    updateNotificationCount();
  }

  setInterval(() => {
    updateMeterStatus();
    updateStats();
    updateDevices();
    updateNotificationCount();
  }, REFRESH_INTERVAL);
  setInterval(() => {
    updateChart();
    updateActivity();
    updateTips();
  }, CHART_REFRESH_INTERVAL);
  document.getElementById("refreshBtn").addEventListener("click", loadAllData);
  window.addEventListener("DOMContentLoaded", () => {
    if (token) loadAllData();
    else window.location.href = "index.html";
  });
})();

document.getElementById("billPaymentBtn")?.addEventListener("click", () => {
  window.location.href = "billing.html";
});
