/**
 * Navigation Configuration
 *
 * Toggle which menu items are visible in the sidebar.
 * Set a menu to `false` to hide it from the navigation.
 */
const navigationConfig: Record<string, boolean> = {
  projects:       true,
  dashboard:      true,
  'object-browser': false,
  'data-explorer':  true,
  reports:        false,
  'report-inventory': true,
  'api-explorer':   false,
  'request-log':    true,
  'rationalization': true,
  'rationalization-analysis': true,
  'object-tree':    true,
};

export default navigationConfig;
