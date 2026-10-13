import { enUS, vi } from "date-fns/locale";
import type { Locale as DateFnsLocale } from "date-fns";

// Locale của date-fns theo ngôn ngữ giao diện: format(d, "PPP", { locale: dateFnsLocale(locale) }).
export function dateFnsLocale(locale: string): DateFnsLocale {
  return locale === "en" ? enUS : vi;
}
