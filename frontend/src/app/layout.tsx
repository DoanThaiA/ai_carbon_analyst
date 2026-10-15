import type { Metadata } from "next";
import { NextIntlClientProvider } from "next-intl";
import { getLocale, getTranslations } from "next-intl/server";
import { Leaf } from "lucide-react";
// Arimo (cùng số đo với Arial, đủ dấu tiếng Việt) tự host — dự phòng cho máy KHÔNG có Arial
// hay font thay thế (vd Ubuntu server render PDF báo cáo gửi email bằng Chromium). Máy có Arial
// vẫn dùng Arial (đứng trước trong stack ở globals.css); trình duyệt chỉ tải Arimo khi cần.
import "@fontsource/arimo/400.css";
import "@fontsource/arimo/400-italic.css";
import "@fontsource/arimo/700.css";
import "@fontsource/arimo/700-italic.css";
import "./globals.css";
import { Header } from "@/components/Header";

// Toàn site dùng DUY NHẤT 1 font: Arial, Arimo, Helvetica, sans-serif (khai báo ở --font-sans
// trong globals.css) — Arial hệ thống, Arimo chỉ là bản dự phòng cùng số đo.

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("meta");
  return { title: t("title"), description: t("description") };
}

export default async function RootLayout({

  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const locale = await getLocale();
  return (
    <html lang={locale}>
      <body className="font-sans min-h-screen selection:bg-primary/20">

        <NextIntlClientProvider>
          <div className="flex flex-col min-h-screen">
            <Header />
            <main className="flex-1 px-4 py-6 md:px-6 md:py-8">{children}</main>
          </div>
        </NextIntlClientProvider>
      </body>
    </html>
  );
}
