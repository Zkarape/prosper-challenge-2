import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { headers } from "next/headers";
import "./globals.css";

const themeBootScript = `(() => {
  try {
    const saved = localStorage.getItem("prosper-theme");
    const mode = saved === "light" || saved === "dark" ? saved : "system";
    const resolved = mode === "system" && matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : mode === "system" ? "light" : mode;
    document.documentElement.dataset.themeMode = mode;
    document.documentElement.dataset.theme = resolved;
  } catch (_) {
    document.documentElement.dataset.themeMode = "system";
    document.documentElement.dataset.theme = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
})();`;

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export async function generateMetadata(): Promise<Metadata> {
  const requestHeaders = await headers();
  const host = requestHeaders.get("host") ?? "localhost:3001";
  const protocol = requestHeaders.get("x-forwarded-proto") ?? (host.startsWith("localhost") ? "http" : "https");
  const origin = `${protocol}://${host}`;

  return {
    metadataBase: new URL(origin),
    title: "Prosper Agent Studio",
    description: "Build, test, and inspect reliable healthcare scheduling agents.",
    openGraph: {
      title: "Prosper Agent Studio",
      description: "Reliable healthcare scheduling, inspected.",
      images: [{ url: `${origin}/og.png`, width: 1733, height: 909 }],
    },
    twitter: {
      card: "summary_large_image",
      title: "Prosper Agent Studio",
      description: "Reliable healthcare scheduling, inspected.",
      images: [`${origin}/og.png`],
    },
  };
}

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head><script dangerouslySetInnerHTML={{ __html: themeBootScript }} /></head>
      <body className={`${geistSans.variable} ${geistMono.variable}`}>{children}</body>
    </html>
  );
}
