import type { Metadata } from "next";
import { Inter, IBM_Plex_Mono, Arimo } from "next/font/google";
import { Leaf } from "lucide-react";
import "./globals.css";
import clsx from "clsx";
import { Header } from "@/components/Header";

const inter = Inter({
  subsets: ["latin", "latin-ext", "vietnamese"],
  weight: ["400", "600", "700", "800"],
  variable: "--font-inter",
});
// Arimo — cùng kích thước chữ (metric-compatible) với Arial, có subset tiếng
// Việt. Báo cáo (.report-shell) dùng font này thay cho Arial hệ thống: Arial
// cài sẵn trên từng máy Windows/macOS khác nhau nên hiển thị/tải PDF bị lỗi
// font tiếng Việt, còn webfont thì mọi máy dùng chung 1 file.
const arimo = Arimo({
  subsets: ["latin", "latin-ext", "vietnamese"],
  variable: "--font-arimo",
});
const ibmPlexMono = IBM_Plex_Mono({ weight: ["400", "500", "600", "700"], subsets: ["latin", "latin-ext", "vietnamese"], variable: "--font-ibm-plex-mono" });

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
    <html lang="en" className={clsx(inter.variable, ibmPlexMono.variable, arimo.variable)}>
      <body className="font-sans min-h-screen selection:bg-primary/20">

        <div className="flex flex-col min-h-screen">
          <Header />
          <main className="flex-1 px-4 py-6 md:px-6 md:py-8">{children}</main>
        </div>
      </body>
    </html>
  );
}
