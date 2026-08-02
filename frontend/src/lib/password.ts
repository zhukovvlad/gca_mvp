/**
 * Генерация и копирование паролей для админ-форм.
 *
 * Пароль генерируется через crypto.getRandomValues (браузерный CSPRNG), НЕ
 * Math.random — это даёт криптостойкую энтропию. Формат вида «Xk7m-Pq9L-vf2Z»:
 * три группы по 4 символа из безопасного алфавита (без визуально похожих 0/O/1/l/I).
 */

const ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789";
const GROUPS = 3;
const GROUP_LEN = 4;

/** Сгенерировать пароль вида «Xk7m-Pq9L-vf2Z» (CSPRNG). */
export function generatePassword(): string {
  const total = GROUPS * GROUP_LEN;
  const cryptoObj = globalThis.crypto;
  if (!cryptoObj?.getRandomValues) {
    throw new Error("crypto.getRandomValues недоступен");
  }
  const chars: string[] = [];
  const alphabetLen = ALPHABET.length;
  // Rejection sampling: исключаем modulo-bias.
  // limit = предел, до которого [0, limit) равномерно делится на alphabetLen.
  const limit = Math.floor(2 ** 32 / alphabetLen) * alphabetLen;
  while (chars.length < total) {
    const buf = new Uint32Array(total - chars.length);
    cryptoObj.getRandomValues(buf);
    for (const b of buf) {
      if (b < limit) chars.push(ALPHABET[b % alphabetLen]);
      if (chars.length === total) break;
    }
  }
  const groups: string[] = [];
  for (let i = 0; i < GROUPS; i++) {
    groups.push(chars.slice(i * GROUP_LEN, (i + 1) * GROUP_LEN).join(""));
  }
  return groups.join("-");
}

/**
 * Скопировать текст в буфер обмена. Возвращает true при успехе.
 * Использует navigator.clipboard, с graceful-fallback на false (например, в
 * небезопасном контексте или при отказе в доступе).
 */
export async function copyToClipboard(text: string): Promise<boolean> {
  try {
    const clipboard = globalThis.navigator?.clipboard;
    if (clipboard?.writeText) {
      await clipboard.writeText(text);
      return true;
    }
  } catch {
    // fallthrough
  }
  return false;
}
