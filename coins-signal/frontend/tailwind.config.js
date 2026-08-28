/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        ink: {
          950: "#0b0f14",
          900: "#111821",
          800: "#1a2330",
          700: "#243044",
        },
        long: "#22c55e",
        short: "#f43f5e",
        accent: "#38bdf8",
      },
      fontFamily: {
        sans: ["var(--font-geist)", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [],
};
