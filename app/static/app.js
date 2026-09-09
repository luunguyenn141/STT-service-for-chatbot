const startButton = document.querySelector("#start-recording");
const stopButton = document.querySelector("#stop-recording");
const transcribeButton = document.querySelector("#transcribe");
const fileInput = document.querySelector("#audio-file");
const keytermsInput = document.querySelector("#keyterms");
const recordingStatus = document.querySelector("#recording-status");
const submissionStatus = document.querySelector("#submission-status");
const selectedFile = document.querySelector("#selected-file");
const audioPreview = document.querySelector("#audio-preview");
const resultCard = document.querySelector("#result-card");
const transcript = document.querySelector("#transcript");

let recorder;
let stream;
let audioChunks = [];
let selectedAudio;
let previewUrl;

function setStatus(element, message, isError = false) {
  element.textContent = message;
  element.classList.toggle("status-error", isError);
}

function setSelectedAudio(file) {
  selectedAudio = file;
  transcribeButton.disabled = !file;
  selectedFile.textContent = file ? `${file.name} · ${(file.size / 1024).toFixed(1)} KB` : "No file selected.";

  if (previewUrl) URL.revokeObjectURL(previewUrl);
  if (file) {
    previewUrl = URL.createObjectURL(file);
    audioPreview.src = previewUrl;
    audioPreview.hidden = false;
  } else {
    audioPreview.removeAttribute("src");
    audioPreview.hidden = true;
  }
}

startButton.addEventListener("click", async () => {
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    setStatus(recordingStatus, "This browser cannot record audio. Upload a file instead.", true);
    return;
  }

  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    audioChunks = [];
    recorder = new MediaRecorder(stream);
    recorder.addEventListener("dataavailable", (event) => {
      if (event.data.size > 0) audioChunks.push(event.data);
    });
    recorder.addEventListener("stop", () => {
      const mimeType = recorder.mimeType || "audio/webm";
      const extension = mimeType.includes("ogg") ? "ogg" : "webm";
      const blob = new Blob(audioChunks, { type: mimeType });
      setSelectedAudio(new File([blob], `recording.${extension}`, { type: mimeType }));
      stream?.getTracks().forEach((track) => track.stop());
      setStatus(recordingStatus, "Recording ready to transcribe.");
    });
    recorder.start();
    startButton.disabled = true;
    stopButton.disabled = false;
    setStatus(recordingStatus, "Recording… speak clearly in Vietnamese.");
  } catch (error) {
    setStatus(recordingStatus, "Microphone access was not granted. Upload a file instead.", true);
  }
});

stopButton.addEventListener("click", () => {
  if (recorder?.state === "recording") recorder.stop();
  startButton.disabled = false;
  stopButton.disabled = true;
});

fileInput.addEventListener("change", () => {
  const [file] = fileInput.files;
  if (file) {
    setSelectedAudio(file);
    setStatus(recordingStatus, "File ready to transcribe.");
  }
});

transcribeButton.addEventListener("click", async () => {
  if (!selectedAudio) return;

  transcribeButton.disabled = true;
  resultCard.hidden = true;
  setStatus(submissionStatus, "Transcribing…");

  const formData = new FormData();
  formData.append("audio", selectedAudio, selectedAudio.name);
  formData.append("language", "vie");
  if (keytermsInput.value.trim()) formData.append("keyterms", keytermsInput.value.trim());

  try {
    const response = await fetch("/api/v1/transcriptions", { method: "POST", body: formData });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload?.error?.message || "Transcription failed. Please try again.");
    }

    transcript.textContent = payload.text || "No transcript returned.";
    document.querySelector("#result-language").textContent = payload.language_code || "Unknown";
    document.querySelector("#result-probability").textContent =
      typeof payload.language_probability === "number" ? `${(payload.language_probability * 100).toFixed(1)}%` : "Not provided";
    document.querySelector("#result-words").textContent = String(payload.words?.length ?? 0);
    document.querySelector("#result-request-id").textContent = payload.request_id;
    resultCard.hidden = false;
    setStatus(submissionStatus, "Transcription complete.");
  } catch (error) {
    setStatus(submissionStatus, error.message || "Transcription failed. Please try again.", true);
  } finally {
    transcribeButton.disabled = !selectedAudio;
  }
});
