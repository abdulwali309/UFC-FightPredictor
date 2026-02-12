import type { Metadata } from "next";
import { IBM_Plex_Mono, Manrope, Space_Grotesk } from "next/font/google";
import Link from "next/link";
import ThemeToggle from "@/components/theme-toggle";
import "./globals.css";

const manrope = Manrope({
  variable: "--font-manrope",
  subsets: ["latin"],
});

const space = Space_Grotesk({
  variable: "--font-space",
  subsets: ["latin"],
});

const plexMono = IBM_Plex_Mono({
  variable: "--font-plexmono",
  subsets: ["latin"],
  weight: "400",
});

export const metadata: Metadata = {
  title: "UFCML Fight Intelligence",
  description: "UFC fight predictions, results tracking, and custom matchup analysis",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className={`${manrope.variable} ${space.variable} ${plexMono.variable} antialiased`}>
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){try{var m=localStorage.getItem('ufcml-theme');if(m==='dark'||m==='light'){document.documentElement.setAttribute('data-theme',m);}else{document.documentElement.removeAttribute('data-theme');}}catch(e){}})();`,
          }}
        />
        <div className="app-shell">
          <div className="bg-orb orb-a" aria-hidden="true" />
          <div className="bg-orb orb-b" aria-hidden="true" />

          <header className="site-header">
            <div className="brand-wrap">
              <p className="kicker">UFC Fight Predictor</p>
              <h1>UFCML</h1>
              <p className="tagline">Data-driven matchup probabilities and event tracking</p>
            </div>
            <div className="header-actions">
              <nav className="main-nav" aria-label="Main">
                <Link href="/">Home</Link>
                <Link href="/upcoming">Upcoming Card</Link>
                <Link href="/results">Fight Results</Link>
                <Link href="/predict">Custom Matchup</Link>
              </nav>
              <ThemeToggle />
            </div>
          </header>

          <main className="content">{children}</main>
        </div>
      </body>
    </html>
  );
}
