/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        surface: '#0f0f12',
        panel: '#1a1a1f',
        panelLight: '#232329',
        accent: '#7c5cff',
        accentSoft: '#9c85ff',
      },
    },
  },
  plugins: [],
};
