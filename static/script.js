let currentThreadId = localStorage.getItem("travel_thread_id") || null;
let latestAnswerMarkdown = "";
let currentAbortController = null;
let isGenerating = false;
let speechUtterance = null;
let isSpeaking = false;

function toggleSpeech() {
    const speakBtn = document.querySelector(".speak-btn");

    if (isSpeaking) {
        window.speechSynthesis.cancel();
        isSpeaking = false;
        speakBtn.textContent = "🔊 Listen";
        return;
    }

    if (!("speechSynthesis" in window)) {
        showError("Text-to-speech isn't supported in this browser. Try Chrome or Edge.");
        return;
    }

    const resultBox = document.getElementById("resultBox");
    const text = resultBox.innerText.trim();

    if (!text) {
        showError("No travel plan available to read aloud.");
        return;
    }

    speechUtterance = new SpeechSynthesisUtterance(text);
    speechUtterance.rate = 1;
    speechUtterance.pitch = 1;

    speechUtterance.onend = () => {
        isSpeaking = false;
        speakBtn.textContent = "🔊 Listen";
    };

    speechUtterance.onerror = () => {
        isSpeaking = false;
        speakBtn.textContent = "🔊 Listen";
    };

    window.speechSynthesis.speak(speechUtterance);
    isSpeaking = true;
    speakBtn.textContent = "⏹ Stop";
}

function stopSpeechIfPlaying() {
    if (isSpeaking) {
        window.speechSynthesis.cancel();
        isSpeaking = false;
        const speakBtn = document.querySelector(".speak-btn");
        if (speakBtn) {
            speakBtn.textContent = "🔊 Listen";
        }
    }
}

function handleSendOrStop() {
    if (isGenerating) {
        stopGeneration();
    } else {
        sendMessage();
    }
}

function stopGeneration() {
    if (currentAbortController) {
        currentAbortController.abort();
    }
}

function startNewTrip() {
    // Clears the current thread so the next message starts a brand new
    // LangGraph thread instead of continuing the old one.
    currentThreadId = null;
    localStorage.removeItem("travel_thread_id");

    stopSpeechIfPlaying();
    hideApprovalPanel();

    document.getElementById("userInput").value = "";
    document.getElementById("resultBox").innerHTML = "";
    document.getElementById("resultSection").classList.add("hidden");
    document.getElementById("threadInfo").textContent = "Thread ID: -";
    latestAnswerMarkdown = "";

    hideError();

    document.getElementById("userInput").focus();
}

function setPrompt(text) {
    document.getElementById("userInput").value = text;
}

function setLoading(isLoading) {
    const sendBtn = document.getElementById("sendBtn");
    const btnText = document.getElementById("btnText");
    const btnLoader = document.getElementById("btnLoader");

    isGenerating = isLoading;
    // The button stays enabled while loading so it can double as a Stop button.
    sendBtn.disabled = false;

    if (isLoading) {
        btnText.textContent = "Stop";
        btnLoader.classList.remove("hidden");
        sendBtn.classList.add("stop-mode");
    } else {
        btnText.textContent = "Generate Plan";
        btnLoader.classList.add("hidden");
        sendBtn.classList.remove("stop-mode");
    }
}

function showError(message) {
    const errorBox = document.getElementById("errorBox");

    errorBox.textContent = message;
    errorBox.classList.remove("hidden");
}

function hideError() {
    const errorBox = document.getElementById("errorBox");

    errorBox.classList.add("hidden");
    errorBox.textContent = "";
}

function showResult(answer, threadId) {
    stopSpeechIfPlaying();

    latestAnswerMarkdown = answer;

    const resultSection = document.getElementById("resultSection");
    const resultBox = document.getElementById("resultBox");
    const threadInfo = document.getElementById("threadInfo");

    if (typeof marked !== "undefined") {
        resultBox.innerHTML = marked.parse(answer);
    } else {
        resultBox.innerText = answer;
    }

    threadInfo.textContent = `Thread ID: ${threadId}`;

    resultSection.classList.remove("hidden");

    resultSection.scrollIntoView({
        behavior: "smooth",
        block: "start"
    });
}

// =========================
// HITL approval flow
// =========================

function handleTravelResult(result) {
    currentThreadId = result.thread_id;
    localStorage.setItem("travel_thread_id", currentThreadId);

    if (result.guardrail_allowed === false) {
        showBlockedMessage(result.answer);
        return;
    }

    if (result.requires_approval) {
        showApprovalDraft(result);
        return;
    }

    // Finalized — no more approval needed.
    hideApprovalPanel();
    showResult(result.answer, result.thread_id);

    // If logged in, this trip was just saved server-side — refresh the panel.
    if (typeof isLoggedIn === "function" && isLoggedIn()) {
        loadTrips();
    }
}

function showBlockedMessage(message) {
    hideApprovalPanel();
    document.getElementById("resultSection").classList.add("hidden");
    showError(message);
}

function showApprovalDraft(result) {
    stopSpeechIfPlaying();

    latestAnswerMarkdown = result.answer;

    const resultSection = document.getElementById("resultSection");
    const resultBox = document.getElementById("resultBox");
    const threadInfo = document.getElementById("threadInfo");

    if (typeof marked !== "undefined") {
        resultBox.innerHTML = marked.parse(result.answer);
    } else {
        resultBox.innerText = result.answer;
    }

    threadInfo.textContent = `Thread ID: ${result.thread_id}`;
    resultSection.classList.remove("hidden");

    const approvalPanel = document.getElementById("approvalPanel");
    approvalPanel.classList.remove("hidden");

    const revisionBadge = document.getElementById("revisionBadge");
    if (result.revision_count && result.revision_count > 0) {
        revisionBadge.textContent = `Revision ${result.revision_count} of ${result.max_revisions}`;
        revisionBadge.classList.remove("hidden");
    } else {
        revisionBadge.classList.add("hidden");
    }

    const agentsInfo = document.getElementById("approvalAgentsInfo");
    if (result.selected_agents && result.selected_agents.length) {
        const niceNames = result.selected_agents.map(a => a.replace(/_agent$/, ""));
        agentsInfo.textContent = "Agents consulted: " + niceNames.join(", ");
    } else {
        agentsInfo.textContent = "";
    }

    document.getElementById("feedbackBox").classList.add("hidden");
    document.getElementById("feedbackInput").value = "";

    resultSection.scrollIntoView({
        behavior: "smooth",
        block: "start"
    });
}

function hideApprovalPanel() {
    document.getElementById("approvalPanel").classList.add("hidden");
    document.getElementById("feedbackBox").classList.add("hidden");
}

function toggleFeedbackBox() {
    document.getElementById("feedbackBox").classList.toggle("hidden");
}

async function approvePlan() {
    await sendApprovalDecision(true, "");
}

async function submitRevision() {
    const feedback = document.getElementById("feedbackInput").value.trim();

    if (!feedback) {
        showError("Please describe what you'd like changed.");
        return;
    }

    await sendApprovalDecision(false, feedback);
}

async function sendApprovalDecision(approved, feedback) {
    hideError();

    const approveBtn = document.querySelector(".approve-btn");
    const rejectBtn = document.querySelector(".reject-btn");
    const submitFeedbackBtn = document.querySelector(".submit-feedback-btn");
    const buttons = [approveBtn, rejectBtn, submitFeedbackBtn].filter(Boolean);

    buttons.forEach(btn => { btn.disabled = true; });

    try {
        const headers = {
            "Content-Type": "application/json",
            ...getAuthHeaders()
        };

        const response = await fetch("/api/travel/approve", {
            method: "POST",
            headers: headers,
            body: JSON.stringify({
                thread_id: currentThreadId,
                approved: approved,
                feedback: feedback
            })
        });

        const data = await response.json();

        if (!response.ok || !data.success) {
            throw new Error(data.error || "Something went wrong.");
        }

        handleTravelResult(data);

    } catch (error) {
        showError(error.message);
    } finally {
        buttons.forEach(btn => { btn.disabled = false; });
    }
}

// =========================
// Send message (initial request)
// =========================

async function sendMessage() {
    hideError();

    const input = document.getElementById("userInput");
    const message = input.value.trim();

    if (!message) {
        showError("Please enter your travel request first.");
        return;
    }

    setLoading(true);
    currentAbortController = new AbortController();

    try {
        // getAuthHeaders() comes from auth.js — returns {} for guests,
        // so this endpoint works identically whether or not the user is signed in.
        const headers = {
            "Content-Type": "application/json",
            ...getAuthHeaders()
        };

        const response = await fetch("/api/travel", {
            method: "POST",
            headers: headers,
            body: JSON.stringify({
                message: message,
                thread_id: currentThreadId
            }),
            signal: currentAbortController.signal
        });

        const data = await response.json();

        if (!response.ok || !data.success) {
            throw new Error(data.error || "Something went wrong.");
        }

        handleTravelResult(data);

    } catch (error) {
        if (error.name === "AbortError") {
            showError("Stopped. Note: since the AI agents run as one continuous step on the server, any already-started flight/hotel/AI calls may still finish in the background, but that result won't be shown here.");
        } else {
            showError(error.message);
        }
    } finally {
        setLoading(false);
        currentAbortController = null;
    }
}

function copyResult() {
    const resultBox = document.getElementById("resultBox");
    const text = resultBox.innerText;

    if (!text) {
        return;
    }

    navigator.clipboard.writeText(text)
        .then(() => {
            const copyBtn = document.querySelector(".copy-btn");
            const oldText = copyBtn.textContent;

            copyBtn.textContent = "Copied!";

            setTimeout(() => {
                copyBtn.textContent = oldText;
            }, 1400);
        })
        .catch(() => {
            showError("Could not copy result.");
        });
}

function downloadPDF() {
    const pdfContent = document.getElementById("pdfContent");

    if (!latestAnswerMarkdown || !pdfContent) {
        showError("No travel plan available to download.");
        return;
    }

    const downloadBtn = document.querySelector(".download-btn");
    const oldText = downloadBtn.textContent;

    downloadBtn.textContent = "Preparing PDF...";
    downloadBtn.disabled = true;

    const options = {
        margin: 0.5,
        filename: "tripcrew-travel-plan.pdf",
        image: {
            type: "jpeg",
            quality: 0.98
        },
        html2canvas: {
            scale: 2,
            useCORS: true,
            backgroundColor: "#ffffff"
        },
        jsPDF: {
            unit: "in",
            format: "a4",
            orientation: "portrait"
        },
        pagebreak: {
            mode: ["avoid-all", "css", "legacy"]
        }
    };

    html2pdf()
        .set(options)
        .from(pdfContent)
        .save()
        .then(() => {
            downloadBtn.textContent = oldText;
            downloadBtn.disabled = false;
        })
        .catch(() => {
            downloadBtn.textContent = oldText;
            downloadBtn.disabled = false;
            showError("Could not download PDF.");
        });
}

document.addEventListener("keydown", function(event) {
    if (event.ctrlKey && event.key === "Enter") {
        sendMessage();
    }
});