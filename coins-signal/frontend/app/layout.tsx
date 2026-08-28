import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Coins Signal",
  description: "Realtime Donchian futures signals — equity % sizing, no volume",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="vi">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link
          href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className="min-h-screen bg-ink-950 text-slate-100 antialiased">
        <div className="pointer-events-none fixed inset-0 bg-[radial-gradient(ellipse_at_top,_rgba(56,189,248,0.08),_transparent_55%)]" />
        <div className="relative mx-auto max-w-5xl px-4 py-8 sm:px-6 sm:py-10">
          <header className="mb-8 flex flex-col gap-2 border-b border-white/10 pb-6 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <p className="font-mono text-xs uppercase tracking-[0.2em] text-accent/80">
                Donchian · 15m
              </p>
              <h1 className="mt-1 text-3xl font-semibold tracking-tight sm:text-4xl">
                Coins Signal
              </h1>
              <p className="mt-2 max-w-xl text-sm text-slate-400">
                Tín hiệu đang mở / đã đóng. Size hiển thị theo % tài sản — không lộ khối lượng lệnh.
              </p>
            </div>
            <div className="font-mono text-xs text-slate-500">poll 5s · mark Binance</div>
          </header>
          {children}
        </div>
      </body>
    </html>
  );
}
