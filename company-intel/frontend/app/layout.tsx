import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Company Intel",
  description: "What materially changed about a listed company, why it matters, and what to watch next — with sources.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Source+Serif+4:opsz,wght@8..60,600&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>
        <div className="shell">
          <nav className="nav">
            <Link href="/" className="brand">
              <span className="brand-mark">◆</span> Company Intel
            </Link>
            <div className="nav-links">
              <Link href="/watchlist">My companies</Link>
              <Link href="/history">History</Link>
              <Link href="/login">Account</Link>
            </div>
          </nav>
          {children}
          <footer className="footer">
            Informational research compiled from public sources, with citations. Not investment advice, a recommendation, or an offer to buy or
            sell securities. Company statements are labelled as such and are not independently verified unless stated.
          </footer>
        </div>
      </body>
    </html>
  );
}
