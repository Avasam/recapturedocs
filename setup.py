import sys

import setuptools

name = 'recapturedocs'

def py_ver_dependencies():
	if sys.version_info < (2, 7):
		yield 'argparse'
		yield 'importlib'

setup_params = dict(
	name = name,
	use_hg_version=True,
	description = 'Library for RecaptureDocs.com',
	author = 'Jason R. Coombs',
	author_email = 'jaraco@jaraco.com',
	url = 'http://www.recapturedocs.com/',
	packages = setuptools.find_packages(),
	include_package_data=True,
	license = 'proprietary',
	classifiers = [
		"Development Status :: 5 - Production / Stable",
		"Programming Language :: Python",
		"Programming Language :: Python :: 2",
		"Programming Language :: Python :: 3",
	],
	entry_points = {
		'console_scripts': [
			'recapture-docs = recapturedocs.server:handle_command_line',
			],
	},
	install_requires=[
		'cherrypy >= 3.2.3dev-20120408, <3.3dev',
		'genshi >= 0.6',
		'boto >= 2.2.2, <2.4dev',
		'PyPDF >= 1.12',
		'keyring >= 0.7.1',
		'jaraco.util >= 5.2',
		'path.py',
		'docutils',
		'jaraco.modb >= 1.0.5',
		'pymongo >= 1.9',
		'jaraco.net >= 2.0.1',
		'httpagentparser >= 1.0.1',
	] + list(py_ver_dependencies()),
	extras_require = {
	},
	dependency_links = [
	],
	tests_require=[
		'pytest',
		'dingus',
	],
	setup_requires=[
		'hgtools',
		'pytest-runner',
	],
)

if __name__ == '__main__':
	setuptools.setup(**setup_params)
