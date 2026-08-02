import { useDropzone } from "react-dropzone";
import { UploadCloud } from "lucide-react";
import { cn } from "@/lib/utils";

interface DropzoneProps {
  onDrop: (files: File[]) => void;
  accept?: Record<string, string[]>;
  multiple?: boolean;
  disabled?: boolean;
  hint?: string;
}

export function Dropzone({
  onDrop,
  accept,
  multiple = true,
  disabled,
  hint = "PDF, JPG, PNG до 20 МБ",
}: DropzoneProps) {
  const { getRootProps, getInputProps, isDragActive, isDragReject } =
    useDropzone({ onDrop, accept, multiple, disabled });

  return (
    <div
      {...getRootProps()}
      className={cn(
        "flex cursor-pointer flex-col items-center justify-center rounded-lg px-6 py-12 text-center transition-colors duration-150 hairline-dashed",
        isDragActive && !isDragReject && "border-accent bg-accent-soft",
        isDragReject && "border-danger bg-danger-soft",
        !isDragActive && "bg-surface hover:bg-surface-hover",
        disabled && "cursor-not-allowed opacity-60"
      )}
    >
      <input {...getInputProps()} />
      <UploadCloud
        size={32}
        className={cn(
          "mb-3",
          isDragActive ? "text-accent" : "text-fg-tertiary"
        )}
      />
      <p className="text-sm font-medium text-fg">
        {isDragActive
          ? isDragReject
            ? "Этот формат не поддерживается"
            : "Отпустите, чтобы загрузить"
          : "Перетащите файлы сюда или нажмите для выбора"}
      </p>
      <p className="mt-1 text-xs text-fg-tertiary">{hint}</p>
    </div>
  );
}
