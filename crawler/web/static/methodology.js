'use strict';
try {
  const preference=localStorage.getItem('klima-theme');
  document.documentElement.dataset.theme=(preference ? preference==='dark' : matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light';
} catch { /* Explicit default light remains available if storage is denied. */ }
