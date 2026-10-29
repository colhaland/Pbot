import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Pbot | Otomasi terkontrol",
  description: "Konsol operasional otomasi multi-website dengan persetujuan manusia.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="id">
      <body>{children}</body>
    </html>
  );
}