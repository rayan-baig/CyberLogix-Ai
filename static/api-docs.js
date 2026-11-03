"use strict";
/* Swagger UI's setup for /docs. FastAPI writes this into an inline
   <script>, which the Content-Security-Policy refuses; as a file it runs
   under the same policy as every other page's code. */
window.ui = SwaggerUIBundle({
  url: "/openapi.json",
  dom_id: "#swagger-ui",
  layout: "BaseLayout",
  deepLinking: true,
  showExtensions: true,
  showCommonExtensions: true,
  presets: [SwaggerUIBundle.presets.apis, SwaggerUIBundle.SwaggerUIStandalonePreset],
});
