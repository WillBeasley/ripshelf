// The Settings form, shared by the Settings layout and its section pages.
import { getContext, setContext } from 'svelte';
import type { SettingsForm } from './form.svelte.ts';

const KEY = Symbol('settings-form');
export const setForm = (form: SettingsForm) => setContext(KEY, form);
export const getForm = () => getContext<SettingsForm>(KEY);
