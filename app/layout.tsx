import type { Metadata } from "next";
import { Inter, Roboto } from "next/font/google";
import "./globals.css";
import { metadataStrings } from "./metadata-strings";

const inter = Inter({ 
  subsets: ["latin"],
  display: 'swap',
  preload: true,
});

const roboto = Roboto({ 
  weight: '400',
  subsets: ["latin", "cyrillic"],
  variable: '--font-roboto',
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
  viewport: {
    width: 'device-width',
    initialScale: 1,
    maximumScale: 5,
    userScalable: true,
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

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const bodyClassName = `${inter.className} ${roboto.variable}`;
  
  return (
    <html lang="ru" suppressHydrationWarning>
      <body className={bodyClassName}>
        {children}
      </body>
    </html>
  );
}
