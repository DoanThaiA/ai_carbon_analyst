"use client";

import { useLocale } from "next-intl";
import { useRouter } from "next/navigation";
import { LOCALE_COOKIE, type Locale } from "@/i18n/config";

// Nút chuyển VI/EN: ghi cookie rồi refresh để server render lại với messages mới.
export function LanguageSwitcher() {
  const locale = useLocale() as Locale;
  const router = useRouter();

  const change = (next: Locale) => {
    if (next === locale) return;
    document.cookie = `${LOCALE_COOKIE}=${next}; path=/; max-age=31536000; samesite=lax`;
    router.refresh();
  };

  return (
    <div className="flex items-center rounded-full bg-white/10 p-0.5 text-xs font-semibold" role="group" aria-label="Language">
      {(["vi", "en"] as const).map((l) => (
        <button
          key={l}
          type="button"
          onClick={() => change(l)}
          aria-pressed={locale === l}
          className={`px-2.5 py-1 rounded-full transition-colors ${locale === l ? "bg-white text-primary-dark" : "text-white/80 hover:text-white"}`}
        >
          {l.toUpperCase()}
        </button>
      ))}
    </div>
  );
}
