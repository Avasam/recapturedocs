from __future__ import absolute_import

import os
import sys
import optparse
import functools
import itertools
from collections import namedtuple
from contextlib import contextmanager
import pkg_resources
from textwrap import dedent
import socket
import urlparse
import inspect

import cherrypy
from genshi.template import TemplateLoader, loader
import genshi
from jaraco.util.string import local_format as lf
import boto

from . import turk
from . import persistence
from . import aws

class JobServer(list):
	tl = TemplateLoader([loader.package(__name__, 'view')])

	@cherrypy.expose
	def index(self):
		tmpl = self.tl.load('main.xhtml')
		message = "Welcome to RecaptureDocs"
		return tmpl.generate(message=message).render('xhtml')

	@staticmethod
	def construct_url(path):
		return urlparse.urljoin(cherrypy.request.base, path)

	@staticmethod
	def is_production():
		return cherrypy.config.get('server.production', False)

	@cherrypy.expose
	def upload(self, file, code):
		if self.is_production() and not code == 'recaptureb1':
			tmpl = self.tl.load('simple.xhtml')
			message = dedent("""
				You must enter a valid invitation code to utilize
				recapturedocs at this time. We're sorry for any
				inconvenience, and we're working hard to have the site
				ready for public use very soon. Hit your back button to
				return to the previous page.
				""").strip()
			return tmpl.generate(message=message).render('xhtml')
		server_url = self.construct_url('/process')
		job = turk.ConversionJob(
			file.file, str(file.content_type), server_url, file.filename,
			)
		self.append(job)
		self.save()
		raise cherrypy.HTTPRedirect(lf("status/{job.id}"))

	@cherrypy.expose
	def status(self, job_id):
		tmpl = self.tl.load('status.xhtml')
		job = self._get_job_for_id(job_id)
		return tmpl.generate(job=job, production=self.is_production()
			).render('xhtml')

	def save(self):
		persistence.save('server', self)

	@cherrypy.expose
	def initiate_payment(self, job_id):
		conn = aws.ConnectionFactory.get_fps_connection()
		job = self._get_job_for_id(job_id)
		job.caller_token = conn.install_caller_instruction()
		job.recipient_token = conn.install_recipient_instruction()
		self.save()
		raise cherrypy.HTTPRedirect(
			self.construct_payment_url(job, conn, job.recipient_token)
			)

	@staticmethod
	def construct_payment_url(job, conn, recipient_token):
		n_pages = len(job)
		params = dict(
			callerKey = os.environ['AWS_ACCESS_KEY_ID'], # My access key
			pipelineName = 'SingleUse',
			returnURL = JobServer.construct_url(lf('/complete_payment/{job.id}')),
			callerReference = job.id,
			paymentReason = lf('RecaptureDocs conversion - {n_pages} pages'),
			transactionAmount = float(job.cost),
			recipientToken = recipient_token,
			)
		url = conn.make_url(**params)
		return url
		
	@cherrypy.expose
	def complete_payment(self, job_id, status, tokenID=None, **params):
		job = self._get_job_for_id(job_id)
		if not status == 'SC':
			tmpl = self.tl.load('declined.xhtml')
			params = genshi.Markup(lf('<!-- {params} -->'))
			res = tmpl.generate(status=status, job=job, params=params)
			return res.render('xhtml')
		end_point_url = JobServer.construct_url(lf('/complete_payment/{job_id}'))
		self.verify_URL_signature(end_point_url, params)
		job.sender_token = tokenID
		conn = aws.ConnectionFactory.get_fps_connection()
		conn.pay(float(job.cost), job.sender_token, job.recipient_token,
			job.caller_token)
		job.authorized = True
		job.register_hits()
		self.save()
		raise cherrypy.HTTPRedirect(lf('/status/{job_id}'))

	def verify_URL_signature(self, end_point_url, params):
		assert params['signatureVersion'] == '2'
		assert params['signatureMethod'] == 'RSA-SHA1'
		#key = self.get_key_from_cert(params['certificateURL'])
		# http://laughingmeme.org/2008/12/30/new-amazon-aws-signature-version-2-is-oauth-compatible/
		# http://github.com/simplegeo/python-oauth2
		# http://lists.dlitz.net/pipermail/pycrypto/2009q3/000112.html
		
		conn = aws.ConnectionFactory.get_fps_connection()
		conn.verify_signature(end_point_url, cherrypy.request.query_string)

	@cherrypy.expose
	def process(self, hitId, assignmentId, workerId=None, turkSubmitTo=None, **kwargs):
		"""
		Fulfill a request of a client who's been sent from AMT. This
		will be rendered in an iFrame, so don't use the template.
		"""
		# rename a few variables to use the PEP-8 syntax
		assignment_id = assignmentId
		hit_id = hitId
		worker_id = workerId
		turk_submit_to = turkSubmitTo
		preview = assignment_id == 'ASSIGNMENT_ID_NOT_AVAILABLE'
		page_url = lf('/image/{hit_id}') if not preview else '/static/Lorem ipsum.pdf'
		tmpl = self.tl.load('retype page.xhtml')
		params = dict(vars())
		del params['self']
		return tmpl.generate(**params).render('xhtml')

	def _get_job_for_id(self, job_id):
		jobs = dict((job.id, job) for job in self)
		return jobs[job_id]

	@cherrypy.expose
	def get_results(self, job_id):
		job = self._get_job_for_id(job_id)
		if not job.is_complete():
			return '<div>Job not complete</div>'
		return job.get_data()

	def _jobs_by_hit_id(self):
		def _hits_for(job):
			hits = getattr(job, 'hits', [])
			return ((hit.id, job) for hit in hits)
		job_hits = itertools.imap(_hits_for, self)
		items = itertools.chain.from_iterable(job_hits)
		#items = list(items); print items
		return dict(items)

	@cherrypy.expose
	def image(self, hit_id):
		# find the appropriate image
		job = self._jobs_by_hit_id()[hit_id]
		if not job: raise cherrypy.NotFound
		cherrypy.response.headers['Content-Type'] = job.content_type
		return job.page_for_hit(hit_id)

	@cherrypy.expose
	def design(self):
		return self.tl.load('design goals.xhtml').generate().render('xhtml')

	def __getstate__(self):
		return list(self)

	def __setstate__(self, items):
		self[:] = items

class Devel(object):
	def __init__(self, server):
		self.server = server

	@cherrypy.expose
	def status(self):
		yield '<div>'
		for job in self.server:
			yield '<div>'
			filename = job.filename
			pages = len(job)
			yield lf('<div style="margin:1em;">Job Filename: {filename} ({pages} pages)')
			yield lf('<div>ID: <a href="/status/{job.id}">{job.id}</a></div>')
			yield lf('<div>Payment authorized: {job.authorized}</div>')
			if not job.authorized:
				yield lf('<div><a href="pay/{job.id}">simulate payment</a></div>')
			yield '<div style="margin-left:1em;">Hits'
			for hit in getattr(job, 'hits', []):
				yield '<div>'
				yield hit.id
				yield '</div>'
			yield '</div>'
			yield '</div>'
		else:
			yield 'no jobs'
		yield '</div>'

	@cherrypy.expose
	def disable_all(self):
		"""
		Disable of all recapture-docs hits (even those not recognized by this
		server).
		"""
		disabled = turk.RetypePageHIT.disable_all()
		del server[:]
		msg = 'Disabled {disabled} HITs (do not forget to remove them from other servers).'
		return lf(msg)

	@cherrypy.expose
	def pay(self, job_id):
		"""
		Force payment for a given job.
		"""
		job = self.server._get_job_for_id(job_id)
		job.authorized = True
		job.register_hits()
		return lf('<a href="/status/{job_id}">Payment simulated; click here for status.</a>')


@contextmanager
def start_server(*configs):
	"""
	The main entry point for the service, regardless of how it's used.
	Takes any number of filename or dictionary objects suitable for
	cherrypy.config.update.
	"""
	global cherrypy, server
	import cherrypy
	# set the socket host, but let other configs override
	host_config = {'global':{'server.socket_host': '::0'}}
	static_dir = pkg_resources.resource_filename('recapturedocs', 'static')
	static_config = {'/static':
			{
			'tools.staticdir.on': True,
			'tools.staticdir.dir': static_dir,
			},}
	configs = list(itertools.chain([host_config, static_config],configs))
	map(cherrypy.config.update, configs)
	persistence.init()
	server = persistence.load('server') or JobServer()
	if hasattr(cherrypy.engine, "signal_handler"):
		cherrypy.engine.signal_handler.subscribe()
	if hasattr(cherrypy.engine, "console_control_handler"):
		cherrypy.engine.console_control_handler.subscribe()
	app = cherrypy.tree.mount(server, '/')
	map(app.merge, configs)
	if not cherrypy.config.get('server.production', False):
		dev_app = cherrypy.tree.mount(Devel(server), '/devel')
		map(dev_app.merge, configs)
		boto.set_stream_logger('recapturedocs')
		aws.ConnectionFactory.production=False
	cherrypy.engine.start()
	yield server
	cherrypy.engine.exit()
	server.save()

def serve(*configs):
	with start_server(*configs):
		cherrypy.engine.block()
	raise SystemExit(0)

def interact(*configs):
	# change some config that's problemmatic in interactive mode
	config = {
		'global':
			{
			'autoreload.on': False,
			'log.screen': False,
			},
		}
	with start_server(config, *configs):
		import code; code.interact(local=globals())

def get_log_directory(appname):
	candidate = os.path.join(sys.prefix, 'var')
	if os.path.isdir(candidate):
		return candidate
	def ensure_exists(func):
		@functools.wraps(func)
		def make_if_not_present():
			dir = func()
			if not os.path.isdir(dir):
				os.makedirs(dir)
			return dir
		return make_if_not_present
	@ensure_exists
	def get_log_root_win32():
		return os.path.join(os.environ['SYSTEMROOT'], 'System32', 'LogFiles', appname)
	@ensure_exists
	def get_log_root_linux2():
		if sys.prefix == '/usr':
			return '/var/' + appname.lower()
		return os.path.join(sys.prefix, 'var', appname.lower())
	getter = locals()['get_log_root_'+sys.platform]
	return getter()

def daemon(*configs):
	from cherrypy.process.plugins import Daemonizer
	appname = 'RecaptureDocs'
	log = os.path.join(get_log_directory(appname), 'log.txt')
	error = os.path.join(get_log_directory(appname), 'error.txt')
	d = Daemonizer(cherrypy.engine, stdout=log, stderr=error)
	d.subscribe()
	with start_server(*configs):
		cherrypy.engine.block()
	
def handle_command_line():
	"%prog <command> [options]"
	usage = inspect.getdoc(handle_command_line)
	parser = optparse.OptionParser(usage=usage)
	options, args = parser.parse_args()
	if not args: parser.error('A command is required')
	cmd = args.pop(0)
	configs = args
	if cmd in globals():
		globals()[cmd](*configs)

if __name__ == '__main__':
	handle_command_line()
