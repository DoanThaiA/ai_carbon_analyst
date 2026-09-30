import type { Metadata } from "next";
import { Leaf } from "lucide-react";
import "./globals.css";
import { Header } from "@/components/Header";

// Toàn site dùng DUY NHẤT 1 font: Arial, Helvetica, sans-serif (khai báo ở --font-sans
// trong globals.css) — font hệ thống, không nạp webfont nào.

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
    <html lang="vi">
      <body className="font-sans min-h-screen selection:bg-primary/20">

        <div className="flex flex-col min-h-screen">
          <Header />
          <main className="flex-1 px-4 py-6 md:px-6 md:py-8">{children}</main>
        </div>
      </body>
    </html>
  );
}
