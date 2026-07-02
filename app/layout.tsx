import type { Metadata, Viewport } from "next";
import { Inter, Lora } from "next/font/google";
import "./globals.css";
import { metadataStrings } from "./metadata-strings";
import { ClientErrorReporter } from "@/components/client-error-reporter";

const inter = Inter({
  subsets: ["latin", "cyrillic"],
  variable: '--font-sans',
  display: 'swap',
  preload: true,
});

const lora = Lora({
  subsets: ["latin", "cyrillic"],
  variable: '--font-serif',
  display: 'swap',
  preload: true,
});

export const metadata: Metadata = {
  title: metadataStrings.siteTitle,
  description: metadataStrings.siteDescription,
  keywords: metadataStrings.siteKeywords,
  authors: [{ name: metadataStrings.siteName }],
  creator: metadataStrings.siteName,
  publisher: metadataStrings.siteName,
  formatDetection: {
    email: false,
    address: false,
    telephone: false,
  },
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL || 'https://your-project.pages.dev'),
  alternates: {
    canonical: '/',
  },
  openGraph: {
    title: metadataStrings.siteTitle,
    description: metadataStrings.siteDescription,
    url: '/',
    siteName: metadataStrings.siteName,
    type: "website",
    locale: "ru_RU",
    countryName: "Russia",
  },
  twitter: {
    card: "summary_large_image",
    title: metadataStrings.siteTitle,
    description: metadataStrings.siteDescription,
  },
  robots: {
    index: true,
    follow: true,
    googleBot: {
      index: true,
      follow: true,
      'max-video-preview': -1,
      'max-image-preview': 'large',
      'max-snippet': -1,
    },
  },
  verification: {
    google: 'your-google-verification-code',
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 5,
  userScalable: true,
  // Extends the page under the notch/home indicator and activates the
  // env(safe-area-inset-*) values the layout compensates with.
  viewportFit: "cover",
  // Chrome/Android: the software keyboard resizes the layout viewport, so
  // bottom-pinned UI (chat composer) stays visible without JS. iOS ignores
  // this; hooks/use-app-height.ts covers it there.
  interactiveWidget: "resizes-content",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const bodyClassName = `${inter.variable} ${lora.variable}`;

  return (
    <html lang="ru" suppressHydrationWarning>
      <body className={bodyClassName}>
        <ClientErrorReporter />
        {children}
      </body>
    </html>
  );
}
