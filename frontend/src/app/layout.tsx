import type { Metadata } from "next";
import { Inter, IBM_Plex_Mono } from "next/font/google";
import { Leaf } from "lucide-react";
import "./globals.css";
import clsx from "clsx";
import { Header } from "@/components/Header";
import { PageShell } from "@/components/PageShell";

const inter = Inter({
  subsets: ["latin"],
  weight: ["400", "600", "700", "800"],
  variable: "--font-inter",
});
const ibmPlexMono = IBM_Plex_Mono({ weight: ["400", "500", "600", "700"], subsets: ["latin"], variable: "--font-ibm-plex-mono" });

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
    <html lang="en" className={clsx(inter.variable, ibmPlexMono.variable)}>
      <body className="font-sans min-h-screen selection:bg-primary/20">

        <div className="flex flex-col min-h-screen">
          <Header />
          <PageShell>{children}</PageShell>
        </div>
      </body>
    </html>
  );
}
