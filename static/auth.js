// Auth state lives in localStorage so the session survives a page refresh.
let authToken = localStorage.getItem("tripcrew_token") || null;
let authUser = JSON.parse(localStorage.getItem("tripcrew_user") || "null");

function getAuthHeaders() {
    if (!authToken) {
        return {};
    }
    return { "Authorization": `Bearer ${authToken}` };
}

function isLoggedIn() {
    return !!authToken;
}

function refreshAuthUI() {
    const loggedOut = document.getElementById("authLoggedOut");
    const loggedIn = document.getElementById("authLoggedIn");
    const nameLabel = document.getElementById("userNameLabel");

    if (isLoggedIn() && authUser) {
        loggedOut.classList.add("hidden");
        loggedIn.classList.remove("hidden");
        nameLabel.textContent = authUser.name || authUser.email;
    } else {
        loggedOut.classList.remove("hidden");
        loggedIn.classList.add("hidden");
        document.getElementById("tripsPanel").classList.add("hidden");
    }
}

function setSession(token, user) {
    authToken = token;
    authUser = user;
    localStorage.setItem("tripcrew_token", token);
    localStorage.setItem("tripcrew_user", JSON.stringify(user));
    refreshAuthUI();
}

function logout() {
    authToken = null;
    authUser = null;
    localStorage.removeItem("tripcrew_token");
    localStorage.removeItem("tripcrew_user");
    // A logged-out session should start a fresh conversation thread too.
    localStorage.removeItem("travel_thread_id");
    if (typeof currentThreadId !== "undefined") {
        currentThreadId = null;
    }
    refreshAuthUI();
}

// =========================
// Modal open/close/tabs
// =========================

function openAuthModal(tab) {
    document.getElementById("authModal").classList.remove("hidden");
    switchAuthTab(tab || "login");
    hideAuthModalError();
}

function closeAuthModal() {
    document.getElementById("authModal").classList.add("hidden");
}

function closeAuthModalOnOverlay(event) {
    if (event.target.id === "authModal") {
        closeAuthModal();
    }
}

function switchAuthTab(tab) {
    const loginForm = document.getElementById("loginForm");
    const registerForm = document.getElementById("registerForm");
    const tabLogin = document.getElementById("tabLogin");
    const tabRegister = document.getElementById("tabRegister");

    hideAuthModalError();

    if (tab === "register") {
        loginForm.classList.add("hidden");
        registerForm.classList.remove("hidden");
        tabLogin.classList.remove("active");
        tabRegister.classList.add("active");
    } else {
        registerForm.classList.add("hidden");
        loginForm.classList.remove("hidden");
        tabRegister.classList.remove("active");
        tabLogin.classList.add("active");
    }
}

function showAuthModalError(message) {
    const el = document.getElementById("authModalError");
    el.textContent = message;
    el.classList.remove("hidden");
}

function hideAuthModalError() {
    const el = document.getElementById("authModalError");
    el.classList.add("hidden");
    el.textContent = "";
}

// =========================
// Login / Register handlers
// =========================

async function handleLogin(event) {
    event.preventDefault();
    hideAuthModalError();

    const email = document.getElementById("loginEmail").value.trim();
    const password = document.getElementById("loginPassword").value;

    try {
        const response = await fetch("/api/auth/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email, password })
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || "Login failed.");
        }

        setSession(data.access_token, data.user);
        closeAuthModal();
        loadTrips();

    } catch (error) {
        showAuthModalError(error.message);
    }

    return false;
}

async function handleRegister(event) {
    event.preventDefault();
    hideAuthModalError();

    const name = document.getElementById("registerName").value.trim();
    const email = document.getElementById("registerEmail").value.trim();
    const password = document.getElementById("registerPassword").value;

    try {
        const response = await fetch("/api/auth/register", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name, email, password })
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || "Registration failed.");
        }

        setSession(data.access_token, data.user);
        closeAuthModal();
        loadTrips();

    } catch (error) {
        showAuthModalError(error.message);
    }

    return false;
}

// =========================
// Trips panel
// =========================

function toggleTripsPanel() {
    const panel = document.getElementById("tripsPanel");
    panel.classList.toggle("hidden");

    if (!panel.classList.contains("hidden")) {
        loadTrips();
    }
}

async function loadTrips() {
    if (!isLoggedIn()) {
        return;
    }

    const tripsList = document.getElementById("tripsList");

    try {
        const response = await fetch("/api/trips", {
            headers: getAuthHeaders()
        });

        const data = await response.json();

        if (!response.ok || !data.success) {
            return;
        }

        if (!data.trips.length) {
            tripsList.innerHTML = `<p class="trips-empty">No saved trips yet. Generate a plan while signed in to save it here.</p>`;
            return;
        }

        tripsList.innerHTML = data.trips.map(trip => `
            <div class="trip-item">
                <button class="trip-open-btn" onclick="openTrip('${trip.thread_id}')">
                    <span class="trip-title">${escapeHtml(trip.title || "Untitled trip")}</span>
                    <span class="trip-date">${new Date(trip.created_at).toLocaleDateString()}</span>
                </button>
                <button class="trip-delete-btn" onclick="deleteTrip('${trip.id}', event)" title="Delete this trip">
                    &times;
                </button>
            </div>
        `).join("");

    } catch (error) {
        // Silently ignore — trips panel just stays empty/stale.
    }
}

async function deleteTrip(tripId, event) {
    event.stopPropagation();

    if (!confirm("Delete this trip? This can't be undone.")) {
        return;
    }

    try {
        const response = await fetch(`/api/trips/${tripId}`, {
            method: "DELETE",
            headers: getAuthHeaders()
        });

        const data = await response.json();

        if (!response.ok || !data.success) {
            throw new Error(data.error || "Could not delete trip.");
        }

        loadTrips();

    } catch (error) {
        showError(error.message);
    }
}

function openTrip(threadId) {
    currentThreadId = threadId;
    localStorage.setItem("travel_thread_id", threadId);
    document.getElementById("tripsPanel").classList.add("hidden");
    document.getElementById("userInput").value = "Continue planning this trip.";
    sendMessage();
}

function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}

// Initialize auth UI on page load
document.addEventListener("DOMContentLoaded", () => {
    refreshAuthUI();
    if (isLoggedIn()) {
        loadTrips();
    }
});