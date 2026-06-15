/** @type {import('next').NextConfig} */
// Конфигурация для Cloudflare Pages и Yandex Cloud
// compress не поддерживается на Cloudflare Pages, поэтому не включаем его
const nextConfig = {
  output: 'standalone', // Для деплоя в Docker/Yandex Cloud Serverless Containers
  serverExternalPackages: ['@supabase/supabase-js'],
  webpack: (config, { isServer }) => {
    // Для Edge Runtime исключаем Node.js-специфичные модули
    if (isServer) {
      config.externals = config.externals || [];
      config.externals.push({
        'mammoth': 'commonjs mammoth',
        'pdf-parse': 'commonjs pdf-parse',
        'word-extractor': 'commonjs word-extractor',
        'pdf-lib': 'commonjs pdf-lib',
      });
    }
    return config;
  },
  
  // Опциональная поддержка прокси для статических ресурсов
  // Если указан NEXT_PUBLIC_PROXY_URL, используем его как assetPrefix
  // Это позволяет загружать статические ресурсы через прокси-сервер
  ...(process.env.NEXT_PUBLIC_PROXY_URL && {
    assetPrefix: process.env.NEXT_PUBLIC_PROXY_URL.replace(/\/$/, ''),
  }),
  
  // Заголовки для статических ресурсов
  async headers() {
    return [
      {
        source: '/_next/static/:path*',
        headers: [
          {
            key: 'Cache-Control',
            value: 'public, max-age=31536000, immutable',
          },
          {
            key: 'X-Content-Type-Options',
            value: 'nosniff',
          },
        ],
      },
      {
        source: '/_next/image',
        headers: [
          {
            key: 'Cache-Control',
            value: 'public, max-age=31536000, immutable',
          },
        ],
      },
    ];
  },
  
  // Оптимизация изображений
  images: {
    formats: ['image/avif', 'image/webp'],
    minimumCacheTTL: 60,
  },
};

export default nextConfig;
