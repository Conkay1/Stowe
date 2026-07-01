export const RECEIPT_ACCEPT = [
  ".jpg",
  ".jpeg",
  ".png",
  ".webp",
  ".heic",
  ".heif",
  ".pdf",
  "image/jpeg",
  "image/png",
  "image/webp",
  "image/heic",
  "image/heif",
  "application/pdf",
].join(",");

const RECEIPT_EXTS = new Set([".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".pdf"]);

function extensionOf(name) {
  const i = name.lastIndexOf(".");
  return i >= 0 ? name.slice(i).toLowerCase() : "";
}

function isReceiptFile(file) {
  return RECEIPT_EXTS.has(extensionOf(file.name || ""));
}

export function splitReceiptFiles(fileList) {
  const accepted = [];
  const rejected = [];
  Array.from(fileList || []).forEach(file => {
    (isReceiptFile(file) ? accepted : rejected).push(file);
  });
  return { accepted, rejected };
}

export function fileKey(file) {
  return [file.name, file.size, file.lastModified].join("|");
}

export function mergeReceiptFiles(existing, incoming) {
  const seen = new Set(existing.map(fileKey));
  const merged = [...existing];
  incoming.forEach(file => {
    const key = fileKey(file);
    if (!seen.has(key)) {
      seen.add(key);
      merged.push(file);
    }
  });
  return merged;
}

export function formatFileSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function hasDraggedFiles(event) {
  const types = event.dataTransfer?.types;
  return types && Array.from(types).includes("Files");
}

export function installReceiptDropTarget({ input, dropTarget, onFiles, onReject }) {
  const handleFiles = fileList => {
    const { accepted, rejected } = splitReceiptFiles(fileList);
    if (accepted.length) onFiles(accepted);
    if (rejected.length) onReject?.(rejected);
  };

  input.addEventListener("change", () => {
    handleFiles(input.files);
    input.value = "";
  });

  let dragDepth = 0;

  dropTarget.addEventListener("dragenter", event => {
    if (!hasDraggedFiles(event)) return;
    event.preventDefault();
    event.stopPropagation();
    dragDepth += 1;
    dropTarget.classList.add("drag-over");
  });

  dropTarget.addEventListener("dragover", event => {
    if (!hasDraggedFiles(event)) return;
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = "copy";
  });

  dropTarget.addEventListener("dragleave", event => {
    if (!hasDraggedFiles(event)) return;
    event.preventDefault();
    event.stopPropagation();
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0) dropTarget.classList.remove("drag-over");
  });

  dropTarget.addEventListener("drop", event => {
    if (!hasDraggedFiles(event)) return;
    event.preventDefault();
    event.stopPropagation();
    dragDepth = 0;
    dropTarget.classList.remove("drag-over");
    handleFiles(event.dataTransfer.files);
  });
}

export async function uploadReceiptFiles(expenseId, files, uploadReceipt, onProgress) {
  const uploaded = [];
  for (let i = 0; i < files.length; i += 1) {
    const fd = new FormData();
    fd.append("file", files[i]);
    uploaded.push(await uploadReceipt(expenseId, fd));
    onProgress?.(i + 1, files.length);
  }
  return uploaded;
}
