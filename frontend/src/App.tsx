import { useCallback, useEffect, useState } from 'react'
import { ApiError, api, type Course } from './api'
import Home from './Home'
import QuizPage from './Quiz'
import DigestPage from './Digest'
import Overview from './Overview'
import { go, pathFor, rememberPath, useRoute } from './route'
import Workspace from './Workspace'
import { useT } from './i18n'

export default function App() {
  const t = useT()
  const route = useRoute()
  const cid = route.name === 'home' || route.name === 'quiz' ? null : route.cid
  const [course, setCourse] = useState<Course | null>(null)
  const [error, setError] = useState<string | null>(null)

  const loadCourse = useCallback(async (id: string) => {
    try {
      return await api.course(id)
    } catch (e) {
      const path = pathFor(id)
      if (e instanceof ApiError && e.status === 404 && path) return await api.open(path)
      throw e
    }
  }, [])

  const refreshCourse = useCallback(() => {
    if (cid) loadCourse(cid).then(setCourse).catch(() => {})
  }, [cid, loadCourse])

  useEffect(() => {
    if (!cid) {
      setCourse(null)
      return
    }
    let live = true
    setError(null)
    loadCourse(cid)
      .then((c) => {
        if (!live) return
        rememberPath(c.id, c.path)
        setCourse(c)
        if (c.id !== cid) go(route.name === 'slide' || route.name === 'digest' ? { ...route, cid: c.id } : { name: 'overview', cid: c.id }, true)
      })
      .catch((e) => live && setError(e instanceof ApiError && e.status === 404
        ? t.courseNotOpen : String(e)))
    return () => {
      live = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cid])

  useEffect(() => {
    document.title = course && cid ? `Gloss · ${course.title}` : 'Gloss'
  }, [course, cid])

  const onOpen = (c: Course) => { rememberPath(c.id, c.path); go({ name: 'overview', cid: c.id }) }
  if (route.name === 'home') return <Home key={route.folder} folder={route.folder} onOpen={onOpen} />
  if (route.name === 'quiz') return <QuizPage key={route.qid} folder={route.folder} qid={route.qid} />
  if (error) return <Home folder="" notice={error} onOpen={onOpen} />
  if (!course || course.id !== cid) return <div className="home"><p className="lead">{t.opening}</p></div>
  if (route.name === 'overview') return <Overview course={course} onRefresh={refreshCourse} />
  if (route.name === 'digest') return <DigestPage course={course} onRefresh={refreshCourse} />
  return <Workspace key={course.id} course={course} page={route.page} thread={route.thread} onCourseChanged={refreshCourse} />
}
