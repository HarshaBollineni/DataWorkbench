import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores([
    'dist',
    'playwright-report',
    'test-results',
    '.vite',
    'src/pages/AgenticSkills.jsx',
    'src/pages/DefineTestPlan.jsx',
    'src/pages/RCA.jsx',
    'src/components/AgentOrgChart.jsx',
  ]),
  {
    files: ['**/*.{js,jsx}'],
    extends: [
      js.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      globals: globals.browser,
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
  },
  {
    // Vendored shadcn/ui primitives co-locate components with their cva()
    // variant maps, which the fast-refresh rule can't treat as constants.
    files: ['src/components/ui/**/*.{js,jsx}'],
    rules: { 'react-refresh/only-export-components': 'off' },
  },
  {
    // Context modules intentionally export both a provider and its matching
    // hook. They are not component-only Fast Refresh boundaries.
    files: ['src/context/**/*.{js,jsx}', 'src/lib/workflowContext.jsx'],
    rules: { 'react-refresh/only-export-components': 'off' },
  },
  {
    files: ['vite.config.js', 'eslint.config.js'],
    languageOptions: { globals: globals.node },
  },
  {
    files: ['tests/**/*.test.js'],
    languageOptions: { globals: globals.node },
  },
])
