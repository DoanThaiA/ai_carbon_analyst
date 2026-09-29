import type { Metadata } from "next";
import { Arimo } from "next/font/google";
import { Leaf } from "lucide-react";
import "./globals.css";
import clsx from "clsx";
import { Header } from "@/components/Header";

// Toàn site dùng DUY NHẤT 1 font: Arial, Helvetica, sans-serif — nạp kèm Arimo
// (webfont cùng kích thước chữ/metric-compatible với Arial, đủ tiếng Việt) đứng
// đầu stack (xem --font-sans trong globals.css). Arial cài sẵn trên máy không
// đồng nhất: Linux/Android không có Arial, Windows gộp "Arial Black" (thiếu dấu
// tiếng Việt) vào family Arial, Helvetica cũ trên macOS thiếu vài ký tự tiếng
// Việt — webfont thì mọi hệ điều hành và bản in PDF dùng chung đúng 1 file.
// Arimo là variable font (400–700); nạp cả kiểu nghiêng để chữ italic là
// nghiêng thật, không phải nghiêng giả do trình duyệt tự bóp.
const arimo = Arimo({
  subsets: ["latin", "latin-ext", "vietnamese"],
  style: ["normal", "italic"],
  display: "swap",
  variable: "--font-arimo",
});

export const metadata: Metadata = {
  title: "Carbon Analyst Dashboard",
  description: "Daily Carbon Intelligence Reports",
};

export default function RootLayout({

  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="vi" className={clsx(arimo.variable)}>
      <body className="font-sans min-h-screen selection:bg-primary/20">

        <div className="flex flex-col min-h-screen">
          <Header />
          <main className="flex-1 px-4 py-6 md:px-6 md:py-8">{children}</main>
        </div>
      </body>
    </html>
  );
}
