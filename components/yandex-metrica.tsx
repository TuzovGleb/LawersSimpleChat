import Script from "next/script";

// Счётчик Яндекс.Метрики.
//
// Рендерится ТОЛЬКО когда задан NEXT_PUBLIC_YM_ID. Значение приходит из
// GitHub Variable, привязанной к окружению деплоя: она существует лишь в
// prod-окружении ("Deploy ENV"). Стейджинг собирается с пустым значением,
// поэтому здесь возвращается null — скрипт метрики даже не загружается и
// трекинга на стейдже нет. Исходники при этом идентичны для обоих окружений.
//
// NEXT_PUBLIC_* инлайнится на этапе сборки, поэтому ID должен приходить
// build-arg'ом (см. Dockerfile и оба deploy-воркфлоу).
const YM_ID = process.env.NEXT_PUBLIC_YM_ID;

export function YandexMetrica() {
  if (!YM_ID) return null;

  return (
    <>
      <Script id="yandex-metrica" strategy="afterInteractive">
        {`
          (function(m,e,t,r,i,k,a){m[i]=m[i]||function(){(m[i].a=m[i].a||[]).push(arguments)};m[i].l=1*new Date();for (var j = 0; j < document.scripts.length; j++) {if (document.scripts[j].src === r) { return; }}k=e.createElement(t),a=e.getElementsByTagName(t)[0],k.async=1,k.src=r,a.parentNode.insertBefore(k,a)})(window, document, 'script', 'https://mc.yandex.ru/metrika/tag.js?id=${YM_ID}', 'ym');
          ym(${YM_ID}, 'init', {ssr:true, webvisor:true, clickmap:true, ecommerce:"dataLayer", accurateTrackBounce:true, trackLinks:true});
        `}
      </Script>
      <noscript>
        <div>
          <img
            src={`https://mc.yandex.ru/watch/${YM_ID}`}
            style={{ position: "absolute", left: "-9999px" }}
            alt=""
          />
        </div>
      </noscript>
    </>
  );
}
