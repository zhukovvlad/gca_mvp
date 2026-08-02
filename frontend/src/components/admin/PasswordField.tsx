import { RefreshCw, Copy, Info } from "lucide-react";
import { toast } from "sonner";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui-domain/Button";
import { generatePassword, copyToClipboard } from "@/lib/password";

interface PasswordFieldProps {
  value: string;
  onChange: (value: string) => void;
  /** Показать подсказку «пароль показывается один раз». */
  hint?: boolean;
  id?: string;
}

/**
 * Поле пароля с кнопками «Сгенерировать» (CSPRNG) и «Скопировать».
 * Используется в формах создания организации и пользователя.
 */
export function PasswordField({ value, onChange, hint = true, id = "password" }: PasswordFieldProps) {
  async function handleCopy() {
    if (!value) return;
    const ok = await copyToClipboard(value);
    if (ok) {
      toast.success("Пароль скопирован — передайте его пользователю безопасным способом");
    } else {
      toast.error("Не удалось скопировать пароль");
    }
  }

  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>Пароль</Label>
      <div className="flex items-center gap-2">
        <Input
          id={id}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="flex-1 font-mono tracking-wide"
          autoComplete="off"
        />
        <Button
          type="button"
          variant="secondary"
          leftIcon={<RefreshCw size={14} />}
          onClick={() => onChange(generatePassword())}
        >
          Сгенерировать
        </Button>
        <Button
          type="button"
          variant="secondary"
          aria-label="Скопировать пароль"
          onClick={handleCopy}
          disabled={!value}
        >
          <Copy size={15} />
        </Button>
      </div>
      {hint && (
        <p className="flex items-center gap-1 text-xs text-fg-tertiary">
          <Info size={13} />
          Пароль показывается один раз — передайте его пользователю безопасным способом.
        </p>
      )}
    </div>
  );
}
