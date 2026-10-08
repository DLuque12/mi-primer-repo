# Buscador de oportunidades inmobiliarias — Lima

Prototipo personal para detectar propiedades en venta con precio máximo de **US$60,000** y priorizar avisos que parezcan estar por debajo del mercado.

## Qué busca

- Departamentos, casas y terrenos.
- Lima centro y sur: Lima Cercado, Breña, La Victoria, Miraflores, San Isidro, Barranco, San Borja, Surquillo, Lince, Magdalena del Mar, Jesús María, Pueblo Libre, San Miguel, Santiago de Surco, San Luis, Chorrillos, San Juan de Miraflores, Villa El Salvador y Lurín.
- Excluye cono norte, Pachacámac y Villa María del Triunfo.
- Tope de compra: **US$60,000**.

## Fuentes iniciales

- Urbania
- Adondevivir

El sistema extrae precio, área, distrito y enlace; calcula US$/m² cuando es posible; elimina duplicados; marca señales de riesgo y asigna un puntaje de oportunidad.

## Automatización

GitHub Actions ejecuta el buscador aproximadamente cada 6 horas y guarda los resultados en:

- `data/latest.md` — resumen legible.
- `data/latest.json` — datos estructurados.

## Importante

El puntaje no sustituye una revisión legal ni registral. Avisos con frases como “sin posesión”, “acciones y derechos”, “ocupado” o similares reciben una penalización de riesgo.

## Próximas etapas

1. Validar que ambos portales entreguen resultados correctamente desde GitHub Actions.
2. Ajustar el puntaje usando históricos de precio por m² por distrito y tipo.
3. Añadir alertas al celular para oportunidades nuevas de alta prioridad.
4. Crear una interfaz web sencilla y, si aporta valor, alojarla en Render.
