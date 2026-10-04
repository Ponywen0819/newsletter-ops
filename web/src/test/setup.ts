import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// jsdom 沒實作 scrollTo（呼叫會印 "Not implemented"）；閱讀器切換單位時會用到
window.scrollTo = () => {}

afterEach(() => cleanup())
