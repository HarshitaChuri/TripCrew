let mediaRecorder = null;
let audioChunks = [];
let isRecording = false;

function isVoiceSupported() {
    return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder);
}

async function toggleRecording() {
    if (!isVoiceSupported()) {
        showError("Voice input isn't supported in this browser. Try Chrome or Edge.");
        return;
    }

    if (isRecording) {
        stopRecording();
    } else {
        await startRecording();
    }
}

async function startRecording() {
    hideError();

    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });

        // Prefer a widely supported mime type; browsers differ in what they support.
        const mimeType = MediaRecorder.isTypeSupported("audio/webm")
            ? "audio/webm"
            : "";

        mediaRecorder = mimeType
            ? new MediaRecorder(stream, { mimeType })
            : new MediaRecorder(stream);

        audioChunks = [];

        mediaRecorder.ondataavailable = (event) => {
            if (event.data.size > 0) {
                audioChunks.push(event.data);
            }
        };

        mediaRecorder.onstop = async () => {
            stream.getTracks().forEach(track => track.stop());
            const audioBlob = new Blob(audioChunks, { type: mediaRecorder.mimeType || "audio/webm" });
            await sendAudioForTranscription(audioBlob);
        };

        mediaRecorder.start();
        isRecording = true;
        setMicUI(true);

    } catch (error) {
        showError("Microphone access was denied or is unavailable.");
    }
}

function stopRecording() {
    if (mediaRecorder && isRecording) {
        mediaRecorder.stop();
        isRecording = false;
        setMicUI(false, true);
    }
}

function setMicUI(recording, transcribing = false) {
    const micBtn = document.getElementById("micBtn");
    const micStatus = document.getElementById("micStatus");

    if (recording) {
        micBtn.classList.add("mic-recording");
        micStatus.textContent = "Listening...";
        micStatus.classList.remove("hidden");
    } else if (transcribing) {
        micBtn.classList.remove("mic-recording");
        micBtn.classList.add("mic-transcribing");
        micStatus.textContent = "Transcribing...";
        micStatus.classList.remove("hidden");
    } else {
        micBtn.classList.remove("mic-recording", "mic-transcribing");
        micStatus.classList.add("hidden");
    }
}

async function sendAudioForTranscription(audioBlob) {
    const formData = new FormData();
    formData.append("audio", audioBlob, "recording.webm");

    try {
        const response = await fetch("/api/voice/transcribe", {
            method: "POST",
            body: formData
        });

        const data = await response.json();

        if (!response.ok || !data.success) {
            throw new Error(data.error || "Could not transcribe audio.");
        }

        const input = document.getElementById("userInput");
        // Append rather than overwrite, in case the user already typed something.
        input.value = input.value.trim() ? `${input.value.trim()} ${data.text}` : data.text;

    } catch (error) {
        showError(error.message);
    } finally {
        setMicUI(false, false);
    }
}
